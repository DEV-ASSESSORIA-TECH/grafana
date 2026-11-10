#!/usr/bin/env python3
"""grafana-alerting: deploy manual do alerting/ deste repositorio no Grafana de producao.

Tudo fica em /opt/grafana-alerting (GRAFANA_ALERTING_HOME):
  repo/        clone local (so a branch configurada; cada execucao faz git fetch)
  live/        arquivos achatados que o Grafana le (montado em /etc/grafana/provisioning/alerting)
  packages/    pacotes .tar.gz (cada deploy e cada backup da pasta live)
  config.json  URL do Grafana, container, branch
  token        token da service account (Admin), 0600
  state.json   pacote aplicado e historico

Fluxo do deploy: fetch -> validacao -> pacote -> ensaio num Grafana temporario (mesma imagem da producao,
rede propria) -> backup de live/ -> copia -> restart do container (so se os arquivos mudaram) -> inibicao
pela API -> conferencia na producao. Falha depois da copia restaura o backup e reinicia de novo.
--skip-test pula o ensaio (aplicacao direta).

Sem comando abre o menu. A cada execucao a ferramenta se atualiza pela branch configurada (--no-update evita).
"""
import argparse
import datetime as dt
import getpass
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("GRAFANA_ALERTING_HOME", "/opt/grafana-alerting"))
REPO, LIVE, PACKAGES = HOME / "repo", HOME / "live", HOME / "packages"
CONFIG, TOKEN, STATE = HOME / "config.json", HOME / "token", HOME / "state.json"
TOOL = Path(__file__).resolve()
MOUNT_TARGET = "/etc/grafana/provisioning/alerting"
TEMP_NAME = "grafana-alerting-temp"  # container e rede do ensaio
KEEP_PACKAGES = 20
DEFAULTS = {
    "url": "https://mon.assessoriatech.com/grafana",
    "container": "",
    "compose_project": "observability",
    "compose_service": "grafana",
    "branch": "main",
}

C = {"ok": "\033[32m", "warn": "\033[33m", "err": "\033[31m", "dim": "\033[2m", "b": "\033[1m", "r": "\033[0m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}


def log(msg):
    print(f"[grafana-alerting] {msg}", flush=True)


def ok(msg):
    log(f"{C['ok']}{msg}{C['r']}")


def warn(msg):
    log(f"{C['warn']}AVISO:{C['r']} {msg}")


class Fail(Exception):
    pass


def run(cmd, check=True, capture=True, **kw):
    res = subprocess.run(cmd, text=True, capture_output=capture, **kw)
    if check and res.returncode != 0:
        out = ((res.stdout or "") + (res.stderr or "")).strip()
        raise Fail(f"{' '.join(map(str, cmd))}: {out[-2000:]}")
    return res


def confirm(question, assume_yes):
    if assume_yes:
        return True
    try:
        return input(f"{question} [s/N] ").strip().lower() in ("s", "sim", "y", "yes")
    except EOFError:
        return False


# ------------------------------------------------------------------ config
def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path, data, mode=0o644):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.chmod(mode)
    tmp.replace(path)


def config():
    return {**DEFAULTS, **load_json(CONFIG, {})}


def token():
    try:
        return TOKEN.read_text().strip()
    except OSError:
        return ""


# ------------------------------------------------------------- auto update
def self_update(cfg, argv):
    """Alinha repo/ com a branch configurada; reexecuta se a propria ferramenta mudou."""
    if not (REPO / ".git").is_dir():
        return
    before = hashlib.sha256(TOOL.read_bytes()).hexdigest()
    branch = cfg["branch"]
    res = run(["git", "-C", REPO, "fetch", "-q", "--filter=blob:none", "origin",
               f"+refs/heads/{branch}:refs/remotes/origin/{branch}"], check=False)
    if res.returncode != 0:
        warn(f"sem atualizacao (git fetch falhou): {res.stderr.strip()[:200]}")
        return
    head = run(["git", "-C", REPO, "rev-parse", "HEAD"]).stdout.strip()
    remote = run(["git", "-C", REPO, "rev-parse", f"origin/{branch}"]).stdout.strip()
    if head == remote:
        return
    run(["git", "-C", REPO, "checkout", "-q", "-B", branch, f"origin/{branch}"])
    run(["git", "-C", REPO, "reset", "-q", "--hard", f"origin/{branch}"])
    log(f"repositorio local atualizado: {head[:7]} -> {remote[:7]} ({branch})")
    if hashlib.sha256(TOOL.read_bytes()).hexdigest() != before:
        log("ferramenta atualizada; reiniciando com a nova versao")
        os.execv(sys.executable, [sys.executable, str(TOOL), *argv, "--no-update"])


# ------------------------------------------------------------------ docker
def prod_container(cfg):
    if cfg["container"]:
        return cfg["container"]
    res = run(["docker", "ps", "-a", "--format", "{{.Names}}",
               "--filter", f"label=com.docker.compose.project={cfg['compose_project']}",
               "--filter", f"label=com.docker.compose.service={cfg['compose_service']}"])
    names = res.stdout.split()
    if len(names) != 1:
        raise Fail(f"container do Grafana nao identificado ({len(names)} encontrados para "
                   f"{cfg['compose_project']}/{cfg['compose_service']}); defina 'container' em {CONFIG}")
    return names[0]


def inspect(name):
    res = run(["docker", "inspect", name], check=False)
    if res.returncode != 0:
        raise Fail(f"container {name} nao encontrado")
    return json.loads(res.stdout)[0]


def mount_ok(info):
    for m in info.get("Mounts") or []:
        if m.get("Destination") == MOUNT_TARGET:
            return Path(m.get("Source", "")).resolve() == LIVE.resolve()
    return False


MOUNT_HELP = f"""
O Grafana de producao ainda nao le {LIVE}. Faca uma vez, no servidor:

  1. Ponto de montagem (pasta vazia; o mount pai e somente leitura e o Docker nao a cria sozinho):
       sudo mkdir /opt/observability/grafana/provisioning/alerting
  2. No compose.yaml, servico grafana, em volumes (depois do mount de provisioning):
       - {LIVE}:{MOUNT_TARGET}:ro
  3. Recriar o container:
       cd /opt/observability && sudo docker compose up -d grafana

Esta ferramenta nao altera /opt/observability.
"""


# ------------------------------------------------------------------- http
def http(url, path, auth, method="GET", body=None, timeout=30):
    req = urllib.request.Request(url.rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": auth, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.load(resp)


def wait_health(url, seconds=180):
    deadline = time.time() + seconds
    last = ""
    while time.time() < deadline:
        try:
            status, body = http(url, "/api/health", "")
            if status == 200 and body.get("database") == "ok":
                return body
        except (OSError, ValueError) as e:
            last = str(e)
        time.sleep(3)
    raise Fail(f"Grafana nao respondeu em {seconds}s ({url}/api/health): {last}")


# ---------------------------------------------------------------- pacotes
def resolve_ref(cfg, ref):
    ref = ref or cfg["branch"]
    run(["git", "-C", REPO, "fetch", "-q", "--filter=blob:none", "origin", ref])
    return run(["git", "-C", REPO, "rev-parse", "FETCH_HEAD"]).stdout.strip(), ref


def build_package(cfg, ref):
    """Valida o ref e gera o pacote: provisioning/alerting (achatado) + src (scripts, contratos, alerting)."""
    sha, ref = resolve_ref(cfg, ref)
    with tempfile.TemporaryDirectory(dir=HOME, prefix="work-") as work:
        work = Path(work)
        src = work / "pkg" / "src"
        src.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(git_archive_bytes(sha))) as tar:
            safe_extract(tar, src)
        log(f"validando {ref} ({sha[:7]})")
        res = run([sys.executable, src / "scripts" / "validate_alerting.py"], check=False)
        print(res.stdout.rstrip())
        if res.returncode != 0:
            raise Fail("validacao falhou; nada foi aplicado")
        prov = work / "pkg" / "provisioning" / "alerting"
        run([sys.executable, src / "scripts" / "alerting_build.py", "--out", prov])
        files = sorted(p.name for p in prov.glob("*.yaml"))
        manifest = {"commit": sha, "ref": ref, "created": dt.datetime.now().isoformat(timespec="seconds"),
                    "provisioning": files,
                    "inhibition": sorted(p.name for p in (src / "alerting" / "inhibition").glob("*.yaml"))}
        (work / "pkg" / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = PACKAGES / f"grafana-alerting-{stamp}-{sha[:7]}.tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            tar.add(work / "pkg", arcname=".")
    prune_packages()
    ok(f"pacote: {out}")
    return out


def git_archive_bytes(sha):
    res = subprocess.run(["git", "-C", REPO, "archive", "--format=tar", sha], capture_output=True)
    if res.returncode != 0:
        raise Fail(f"git archive {sha[:7]}: {res.stderr.decode(errors='replace')[-500:]}")
    return res.stdout


def safe_extract(tar, dest):
    if hasattr(tarfile, "data_filter"):
        tar.extractall(dest, filter="data")
    else:  # Python sem filtros: recusa caminhos fora do destino
        for m in tar.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts or m.issym() or m.islnk():
                raise Fail(f"pacote com caminho invalido: {m.name}")
        tar.extractall(dest)


def prune_packages():
    for prefix in ("grafana-alerting-", "live-backup-"):
        old = sorted(PACKAGES.glob(f"{prefix}*.tar.gz"))[:-KEEP_PACKAGES]
        for p in old:
            p.unlink()


def extract(package, dest):
    with tarfile.open(package) as tar:
        safe_extract(tar, dest)
    manifest = load_json(dest / "MANIFEST.json", None)
    if not manifest:
        raise Fail(f"{package}: pacote sem MANIFEST.json")
    return manifest


# ------------------------------------------------------------------ ensaio
def cleanup_temp(quiet=True):
    """Remove o container e a rede do ensaio (nunca toca em outros containers ou redes)."""
    run(["docker", "rm", "-f", TEMP_NAME], check=False)
    run(["docker", "network", "rm", TEMP_NAME], check=False)
    if not quiet:
        ok(f"container e rede {TEMP_NAME} removidos")


def cleanup_work():
    """Sobras de execucoes interrompidas (as pastas de trabalho somem sozinhas no fim de cada execucao)."""
    for w in HOME.glob("work-*"):
        shutil.rmtree(w, ignore_errors=True)


def rehearse(cfg, pkg_dir):
    """Sobe um Grafana temporario (mesma imagem da producao, rede propria), carrega o pacote e confere."""
    image = inspect(prod_container(cfg))["Config"]["Image"]
    log(f"ensaio num Grafana temporario ({TEMP_NAME}, imagem {image})")
    cleanup_temp()
    prov = pkg_dir / "temp-provisioning"
    (prov / "datasources").mkdir(parents=True)
    shutil.copytree(pkg_dir / "provisioning" / "alerting", prov / "alerting")
    sys.path.insert(0, str(pkg_dir / "src" / "scripts"))
    from contracts import DATASOURCES  # UIDs de producao
    sys.path.pop(0)
    ds = [{"name": f"temp-{group}", "type": group, "uid": uid, "url": f"http://{group}.invalid", "access": "proxy"}
          for group, uid in DATASOURCES.items()]
    (prov / "datasources" / "temp.yaml").write_text(json.dumps({"apiVersion": 1, "datasources": ds}))
    for p in [prov, *prov.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    try:
        run(["docker", "network", "create", "--label", "grafana-alerting=temp", TEMP_NAME])
        run(["docker", "run", "-d", "--name", TEMP_NAME, "--network", TEMP_NAME,
             "--label", "grafana-alerting=temp",
             "-e", "GF_ANALYTICS_REPORTING_ENABLED=false", "-e", "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
             "-e", "GF_PLUGINS_PREINSTALL_DISABLED=true",
             "-v", f"{prov}:/etc/grafana/provisioning:ro", image])
        ip = None
        for _ in range(30):
            nets = inspect(TEMP_NAME)["NetworkSettings"]["Networks"]
            ip = (nets.get(TEMP_NAME) or {}).get("IPAddress")
            if ip:
                break
            time.sleep(1)
        url = f"http://{ip}:3000"
        try:
            wait_health(url, 120)
        except Fail:
            logs = run(["docker", "logs", "--tail", "40", TEMP_NAME], check=False)
            raise Fail("o Grafana temporario nao subiu com o pacote (em producao ele tambem nao subiria):\n"
                       + (logs.stdout + logs.stderr)[-3000:])
        env = {**os.environ, "GRAFANA_TOKEN": "", "NO_PROXY": "*", "no_proxy": "*"}
        env.pop("GRAFANA_TOKEN")
        scripts = pkg_dir / "src" / "scripts"
        res = run([sys.executable, scripts / "alerting_inhibition.py", "--url", url, "--user", "admin",
                   "--password", "admin", "--apply", "--prune"], check=False, env=env)
        if res.returncode != 0:
            raise Fail(f"ensaio: inibicao falhou:\n{res.stdout}{res.stderr}")
        res = run([sys.executable, scripts / "alerting_check.py", "--url", url], check=False, env=env)
        if res.returncode != 0:
            raise Fail(f"ensaio: conferencia falhou:\n{res.stdout[-3000:]}{res.stderr[-1000:]}")
        ok("ensaio ok: " + res.stdout.strip().splitlines()[-1])
    finally:
        cleanup_temp()


# ------------------------------------------------------------------ aplicar
def live_files():
    return {p.name: p.read_bytes() for p in LIVE.glob("*.yaml")}


def backup_live():
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = PACKAGES / f"live-backup-{stamp}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for p in sorted(LIVE.glob("*.yaml")):
            tar.add(p, arcname=p.name)
    return out


def write_live(files):
    for p in LIVE.glob("*.yaml"):
        if p.name not in files:
            p.unlink()
    for name, data in files.items():
        tmp = LIVE / f".{name}.tmp"
        tmp.write_bytes(data)
        tmp.chmod(0o644)  # o Grafana (uid 472) precisa ler; sem leitura ele nao sobe
        tmp.replace(LIVE / name)
    LIVE.chmod(0o755)


def restart(cfg):
    name = prod_container(cfg)
    log(f"reiniciando o container {name}")
    run(["docker", "restart", name])
    wait_health(cfg["url"])
    ok("Grafana de producao respondendo")


def prod_env(cfg):
    tok = token()
    if not tok:
        raise Fail(f"token ausente ({TOKEN}); rode 'grafana-alerting setup'")
    return {**os.environ, "GRAFANA_TOKEN": tok, "GRAFANA_URL": cfg["url"]}


def apply_inhibition(cfg, pkg_dir):
    res = run([sys.executable, pkg_dir / "src" / "scripts" / "alerting_inhibition.py", "--url", cfg["url"],
               "--apply", "--prune"], check=False, env=prod_env(cfg))
    print(res.stdout.rstrip())
    if res.returncode != 0:
        raise Fail(f"inibicao falhou: {res.stderr.strip()[-1000:]}")


def check_prod(cfg, pkg_dir):
    res = run([sys.executable, pkg_dir / "src" / "scripts" / "alerting_check.py", "--url", cfg["url"]],
              check=False, env=prod_env(cfg))
    if res.returncode != 0:
        raise Fail(f"conferencia na producao falhou:\n{res.stdout[-3000:]}{res.stderr[-1000:]}")
    ok("producao conferida: " + res.stdout.strip().splitlines()[-1])


def apply_package(cfg, package, skip_test, assume_yes):
    package = Path(package).resolve()
    info = inspect(prod_container(cfg))
    if not mount_ok(info):
        print(MOUNT_HELP)
        raise Fail("mount ausente; nada foi aplicado")
    state = load_json(STATE, {"history": []})
    with tempfile.TemporaryDirectory(dir=HOME, prefix="work-") as pkg_dir:
        pkg_dir = Path(pkg_dir)
        manifest = extract(package, pkg_dir)
        new = {name: (pkg_dir / "provisioning" / "alerting" / name).read_bytes() for name in manifest["provisioning"]}
        old = live_files()
        changed = sorted(n for n in set(new) | set(old) if new.get(n) != old.get(n))
        log(f"pacote {package.name}: {manifest['ref']} ({manifest['commit'][:7]})")
        for n in changed:
            log(f"  {'novo    ' if n not in old else 'removido' if n not in new else 'alterado'} {n}")
        if not changed:
            log("  arquivos de provisionamento iguais aos aplicados (sem restart)")
        if skip_test:
            warn("ensaio pulado (--skip-test): aplicando direto na producao")
        else:
            rehearse(cfg, pkg_dir)
        if not confirm("Aplicar na producao?", assume_yes):
            raise Fail("cancelado; nada foi aplicado")
        backup = backup_live()
        log(f"backup de live/: {backup}")
        try:
            if changed:
                write_live(new)
                restart(cfg)
            apply_inhibition(cfg, pkg_dir)
            check_prod(cfg, pkg_dir)
        except (Fail, OSError) as e:
            log(f"{C['err']}falhou:{C['r']} {e}")
            rollback_after_failure(cfg, state, backup, changed)
            raise Fail("deploy desfeito; a producao voltou ao estado anterior")
    entry = {"package": package.name, "commit": manifest["commit"], "ref": manifest["ref"],
             "applied": dt.datetime.now().isoformat(timespec="seconds"), "tested": not skip_test}
    state["deployed"] = entry
    state["history"] = ([entry] + state.get("history", []))[:50]
    save_json(STATE, state)
    ok(f"aplicado: {manifest['ref']} ({manifest['commit'][:7]})")


def rollback_after_failure(cfg, state, backup, changed):
    log("restaurando o estado anterior")
    with tarfile.open(backup) as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    write_live(files)
    try:
        if changed:
            restart(cfg)
        prev = (state.get("deployed") or {}).get("package")
        if prev and (PACKAGES / prev).exists():
            with tempfile.TemporaryDirectory(dir=HOME, prefix="work-") as d:
                extract(PACKAGES / prev, Path(d))
                apply_inhibition(cfg, Path(d))
    except Fail as e:
        warn(f"restauracao incompleta, verifique manualmente: {e}")


# ---------------------------------------------------------------- comandos
def cmd_setup(cfg, args):
    HOME.mkdir(parents=True, exist_ok=True)
    for d in (LIVE, PACKAGES):
        d.mkdir(exist_ok=True)
        d.chmod(0o755)
    stored = load_json(CONFIG, {})
    if args.branch:
        stored["branch"] = args.branch
    if args.url:
        stored["url"] = args.url.rstrip("/")
    if args.container:
        stored["container"] = args.container
    if "url" not in stored and sys.stdin.isatty():
        answer = input(f"URL do Grafana [{DEFAULTS['url']}]: ").strip()
        stored["url"] = (answer or DEFAULTS["url"]).rstrip("/")
    save_json(CONFIG, stored)
    cfg = config()
    log(f"configuracao: {CONFIG}")

    name = prod_container(cfg)
    info = inspect(name)
    ok(f"container de producao: {name} ({info['Config']['Image']})")

    if args.reset_token or not token():
        if not sys.stdin.isatty():
            raise Fail("sem token e sem terminal para pedir; rode 'grafana-alerting setup' interativo")
        print("Token de service account com papel Admin (Administration > Users and access > Service accounts).")
        tok = getpass.getpass("Token: ").strip()
        if not tok:
            raise Fail("token vazio")
        TOKEN.write_text(tok + "\n")
        TOKEN.chmod(0o600)
    auth = f"Bearer {token()}"
    try:
        http(cfg["url"], "/api/folders", auth)
        http(cfg["url"], "/apis/notifications.alerting.grafana.app/v1beta1/namespaces/default/inhibitionrules", auth)
        ok(f"token valido em {cfg['url']}")
    except urllib.error.HTTPError as e:
        raise Fail(f"token recusado ({e.code}) em {cfg['url']}; refaca com 'grafana-alerting setup --reset-token'")
    except OSError as e:
        raise Fail(f"Grafana inacessivel em {cfg['url']}: {e}")

    if mount_ok(info):
        ok(f"mount ok: {LIVE} -> {MOUNT_TARGET}")
    else:
        print(MOUNT_HELP)
        warn("mount pendente: o deploy recusa aplicar ate ele existir")


def cmd_status(cfg, args):
    state = load_json(STATE, {})
    head = run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], check=False).stdout.strip()
    print(f"\n {C['b']}GRAFANA ALERTING{C['r']}  {HOME}")
    print(f"  Ferramenta    {cfg['branch']} @ {head or '?'}")
    print(f"  Grafana       {cfg['url']}")
    try:
        name = prod_container(cfg)
        info = inspect(name)
        running = info["State"]["Status"]
        print(f"  Container     {name} ({running})  {info['Config']['Image'][:60]}")
        print(f"  Mount         {'ok' if mount_ok(info) else C['warn'] + 'pendente (rode setup)' + C['r']}")
    except Fail as e:
        print(f"  Container     {C['err']}{e}{C['r']}")
    print(f"  Token         {'ok' if token() else C['warn'] + 'ausente' + C['r']}")
    dep = state.get("deployed")
    if dep:
        print(f"  Aplicado      {dep['ref']} @ {dep['commit'][:7]} em {dep['applied']}"
              f"{'' if dep.get('tested') else ' (sem ensaio)'}")
        remote = run(["git", "-C", REPO, "rev-parse", f"origin/{cfg['branch']}"], check=False).stdout.strip()
        if remote and remote != dep["commit"]:
            diff = run(["git", "-C", REPO, "diff", "--stat", dep["commit"], remote, "--",
                        "alerting", "contracts/alerts"], check=False).stdout.strip()
            print(f"  Pendente      {cfg['branch']} @ {remote[:7]}")
            for line in diff.splitlines():
                print(f"                {line}")
        elif remote:
            print(f"  Pendente      nada ({cfg['branch']} ja aplicada)")
    else:
        print("  Aplicado      nada ainda")
    print()


def cmd_package(cfg, args):
    build_package(cfg, args.ref)


def cmd_test(cfg, args):
    package = build_package(cfg, args.ref)
    with tempfile.TemporaryDirectory(dir=HOME, prefix="work-") as d:
        extract(package, Path(d))
        rehearse(cfg, Path(d))


def cmd_deploy(cfg, args):
    package = build_package(cfg, args.ref)
    apply_package(cfg, package, args.skip_test, args.yes)


def cmd_apply(cfg, args):
    apply_package(cfg, args.package, args.skip_test, args.yes)


def cmd_rollback(cfg, args):
    history = load_json(STATE, {}).get("history") or []
    if len(history) < 2:
        raise Fail("nao ha deploy anterior no historico")
    prev = history[1]
    package = PACKAGES / prev["package"]
    if not package.exists():
        raise Fail(f"pacote {prev['package']} nao existe mais em {PACKAGES}")
    log(f"voltando para {prev['ref']} ({prev['commit'][:7]}), aplicado em {prev['applied']}")
    apply_package(cfg, package, args.skip_test, args.yes)


def cmd_list(cfg, args):
    state = load_json(STATE, {})
    current = (state.get("deployed") or {}).get("package")
    for p in sorted(PACKAGES.glob("*.tar.gz"), reverse=True):
        print(f"{'*' if p.name == current else ' '} {p}")


def cmd_cleanup(cfg, args):
    cleanup_temp(quiet=False)
    cleanup_work()


def cmd_update(cfg, args):
    pass  # a atualizacao roda antes de todo comando


MENU = [
    ("1", "Ver estado", "status"),
    ("2", "Deploy (com ensaio)", "deploy"),
    ("3", "Deploy direto (sem ensaio)", "deploy-direct"),
    ("4", "So ensaiar (nao toca na producao)", "test"),
    ("5", "So gerar pacote", "package"),
    ("6", "Voltar ao deploy anterior", "rollback"),
    ("7", "Listar pacotes", "list"),
    ("8", "Remover temporarios", "cleanup"),
    ("9", "Setup / trocar token", "setup"),
]


def menu(cfg, parser):
    while True:
        cmd_status(cfg, None)
        for key, label, _ in MENU:
            print(f"  [{key}] {label}")
        try:
            choice = input("\n  Opcao (0 sai): ").strip()
        except EOFError:
            return
        if choice in ("", "0"):
            return
        action = next((a for k, _, a in MENU if k == choice), None)
        if not action:
            continue
        argv = ["deploy", "--skip-test"] if action == "deploy-direct" else [action]
        if action == "setup":
            argv.append("--reset-token")
        try:
            dispatch(cfg, parser.parse_args([*argv, "--no-update"]))
        except Fail as e:
            log(f"{C['err']}ERRO:{C['r']} {e}")
        input("\n  Enter para voltar ao menu")


def dispatch(cfg, args):
    {"setup": cmd_setup, "status": cmd_status, "deploy": cmd_deploy, "apply": cmd_apply, "test": cmd_test,
     "package": cmd_package, "rollback": cmd_rollback, "list": cmd_list, "cleanup": cmd_cleanup,
     "update": cmd_update}[args.command](cfg, args)


def main():
    parser = argparse.ArgumentParser(prog="grafana-alerting", description=__doc__.splitlines()[0])
    parser.add_argument("--no-update", action="store_true", help="nao se atualiza antes de executar")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("setup", help="configura URL, container e token; confere o mount")
    p.add_argument("--branch")
    p.add_argument("--url")
    p.add_argument("--container")
    p.add_argument("--reset-token", action="store_true")
    sub.add_parser("status", help="estado da producao e o que esta pendente")
    for name, text in (("deploy", "fetch, valida, empacota, ensaia e aplica"),
                       ("test", "fetch, valida, empacota e ensaia (nao toca na producao)"),
                       ("package", "fetch, valida e gera o pacote")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--ref", help="branch, tag ou commit (padrao: branch configurada)")
        if name == "deploy":
            p.add_argument("--skip-test", action="store_true", help="aplica direto, sem o ensaio")
            p.add_argument("--yes", "-y", action="store_true", help="nao pergunta antes de aplicar")
    p = sub.add_parser("apply", help="aplica um pacote existente")
    p.add_argument("package")
    p.add_argument("--skip-test", action="store_true", help="aplica direto, sem o ensaio")
    p.add_argument("--yes", "-y", action="store_true")
    p = sub.add_parser("rollback", help="aplica de novo o pacote do deploy anterior")
    p.add_argument("--skip-test", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")
    sub.add_parser("list", help="lista os pacotes")
    sub.add_parser("cleanup", help="remove container/rede temporarios e sobras")
    sub.add_parser("update", help="so atualiza a ferramenta")
    for sp in sub.choices.values():
        sp.add_argument("--no-update", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("[grafana-alerting] ERRO: execute com sudo.")
    cfg = config()
    if not args.no_update:
        self_update(cfg, sys.argv[1:])
        cfg = config()
    if HOME.is_dir():
        cleanup_work()
    try:
        if args.command:
            dispatch(cfg, args)
        else:
            menu(cfg, parser)
    except Fail as e:
        log(f"{C['err']}ERRO:{C['r']} {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        cleanup_temp()
        sys.exit(130)
    except (OSError, ValueError, KeyError) as e:
        cleanup_temp()
        log(f"{C['err']}ERRO inesperado:{C['r']} {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
# Instala ou atualiza a ferramenta de deploy do alerting (grafana-alerting) num servidor Ubuntu.
# Cria /opt/grafana-alerting, clona o repositorio (so a branch, sem historico de blobs), instala as
# dependencias e o comando /usr/local/sbin/grafana-alerting, e segue para 'grafana-alerting setup'.
# Nao toca em nenhuma outra pasta de /opt nem no Grafana: o mount no compose e feito por voce (o setup mostra).
set -euo pipefail

REPO_URL=${GRAFANA_ALERTING_REPO:-https://github.com/DEV-ASSESSORIA-TECH/grafana.git}
HOME_DIR=${GRAFANA_ALERTING_HOME:-/opt/grafana-alerting}
BIN=/usr/local/sbin/grafana-alerting
branch=main
setup_args=()
while (($#)); do
  case "$1" in
    --branch) branch=${2:?falta valor para --branch}; shift 2; continue ;;
    --branch=*) branch=${1#*=} ;;
    -h|--help)
      printf 'Uso: sudo bash setup.sh [--branch BRANCH] [opcoes do grafana-alerting setup]\n'; exit 0 ;;
    *) setup_args+=("$1") ;;
  esac
  shift
done

log() { printf '[grafana-alerting] %s\n' "$*"; }
fail() { printf '[grafana-alerting] ERRO: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "execute com sudo."
command -v docker >/dev/null 2>&1 || fail "docker nao encontrado."

missing=()
command -v git >/dev/null 2>&1 || missing+=(git)
command -v curl >/dev/null 2>&1 || missing+=(curl)
command -v python3 >/dev/null 2>&1 || missing+=(python3)
python3 -c 'import yaml' >/dev/null 2>&1 || missing+=(python3-yaml)
if ((${#missing[@]})); then
  log "instalando: ${missing[*]}"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}" >/dev/null
fi

install -d -m 0755 "$HOME_DIR" "$HOME_DIR/live" "$HOME_DIR/packages"
if [[ -d $HOME_DIR/repo/.git ]]; then
  log "atualizando o repositorio local ($branch)"
  git -C "$HOME_DIR/repo" remote set-url origin "$REPO_URL"
  git -C "$HOME_DIR/repo" fetch -q --filter=blob:none origin "+refs/heads/$branch:refs/remotes/origin/$branch"
  git -C "$HOME_DIR/repo" checkout -q -B "$branch" "origin/$branch"
  git -C "$HOME_DIR/repo" reset -q --hard "origin/$branch"
else
  log "clonando $REPO_URL ($branch) em $HOME_DIR/repo"
  git clone -q --filter=blob:none --single-branch --branch "$branch" "$REPO_URL" "$HOME_DIR/repo"
fi
chmod 0755 "$HOME_DIR/repo/deploy/grafana-alerting.py"
ln -sfn "$HOME_DIR/repo/deploy/grafana-alerting.py" "$BIN"
log "comando instalado: $BIN"

exec "$BIN" setup --branch "$branch" --no-update "${setup_args[@]}"

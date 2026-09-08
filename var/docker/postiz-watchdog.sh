#!/bin/sh
# Detect real Postiz process and Temporal worker health. PM2 status and the
# orchestrator HTTP health endpoint are insufficient because both can stay
# green when no worker is polling Temporal task queues.
set -u

PROJECT=${POSTIZ_COMPOSE_PROJECT:-l4162hyamizty2n0eyj0gjxr}
REQUIRED_QUEUES=${POSTIZ_REQUIRED_QUEUES:-"main instagram"}
STATE=${POSTIZ_WATCHDOG_STATE:-/var/lib/postiz-watchdog}
NEED_FAILURES=${POSTIZ_WATCHDOG_FAILURES:-2}
COOLDOWN=${POSTIZ_WATCHDOG_COOLDOWN:-1800}

mkdir -p "$STATE"

log() {
  logger -t postiz-watchdog "$1" 2>/dev/null || true
  printf '%s %s\n' "$(date -Is)" "$1" >> "$STATE/log"
}

container_for() {
  docker ps \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$1" \
    --format '{{.Names}}' | head -n 1
}

container_is_healthy() {
  [ -n "$1" ] &&
    [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ] &&
    [ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null)" != unhealthy ]
}

record_success() {
  component=$1
  failures_file="$STATE/$component-failures"
  previous=$(cat "$failures_file" 2>/dev/null || printf '0')
  if [ "$previous" -gt 0 ]; then
    log "$component снова здоров после $previous провалов"
  fi
  printf '0\n' > "$failures_file"
}

overdue_queue_count() {
  postgres_container=$1
  docker exec "$postgres_container" sh -lc \
    'psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-postiz-db}" -Atqc "SELECT count(*) FROM \"Post\" WHERE state::text = chr(81)||chr(85)||chr(69)||chr(85)||chr(69) AND \"deletedAt\" IS NULL AND \"publishDate\" < now();"' \
    2>/dev/null
}

restart_component() {
  component=$1
  app_container=$2
  postgres_container=$3
  failures_file="$STATE/$component-failures"
  last_restart_file="$STATE/$component-last-restart"

  failures=$(( $(cat "$failures_file" 2>/dev/null || printf '0') + 1 ))
  printf '%s\n' "$failures" > "$failures_file"
  log "$component нездоров (подряд провалов: $failures)"

  [ "$failures" -ge "$NEED_FAILURES" ] || return 0

  now=$(date +%s)
  last_restart=$(cat "$last_restart_file" 2>/dev/null || printf '0')
  if [ $(( now - last_restart )) -lt "$COOLDOWN" ]; then
    log "$component: действует cooldown, повторный рестарт не выполняю"
    return 0
  fi

  if [ "$component" = orchestrator ]; then
    overdue=$(overdue_queue_count "$postgres_container") || {
      log "orchestrator: не удалось проверить просроченную очередь, рестарт заблокирован"
      return 0
    }
    if [ "$overdue" -gt 0 ]; then
      log "orchestrator: найдено просроченных публикаций: $overdue; автоматический рестарт заблокирован"
      return 0
    fi
  fi

  if docker exec "$app_container" pm2 restart "$component" --update-env >/dev/null 2>&1; then
    printf '%s\n' "$now" > "$last_restart_file"
    printf '0\n' > "$failures_file"
    log "$component: PM2-рестарт выполнен"
  else
    log "$component: PM2-рестарт завершился ошибкой"
  fi
}

app_container=$(container_for postiz)
temporal_container=$(container_for temporal)
postgres_container=$(container_for postgres)

# A stopped deployment is intentional from the watchdog's point of view.
if ! container_is_healthy "$app_container"; then
  exit 0
fi

if docker exec "$app_container" ss -ltn 2>/dev/null | grep -q ':3000 '; then
  record_success backend
else
  restart_component backend "$app_container" "$postgres_container"
fi

# Do not penalize the orchestrator while Temporal itself is unavailable.
if ! container_is_healthy "$temporal_container"; then
  exit 0
fi

orchestrator_healthy=true
for queue in $REQUIRED_QUEUES; do
  description=$(docker exec "$temporal_container" temporal task-queue describe \
    --address temporal:7233 --task-queue "$queue" --output json 2>/dev/null) || {
      orchestrator_healthy=false
      break
    }

  printf '%s' "$description" | jq -e '
    [.pollers[]?.taskQueueType] as $types |
    ($types | index("workflow")) != null and
    ($types | index("activity")) != null
  ' >/dev/null 2>&1 || {
    orchestrator_healthy=false
    break
  }
done

if [ "$orchestrator_healthy" = true ]; then
  record_success orchestrator
else
  restart_component orchestrator "$app_container" "$postgres_container"
fi

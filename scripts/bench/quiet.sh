# Refuse to measure on a contended board. Sourced, not run:
#   . "$(dirname "$0")/quiet.sh"; require_quiet
#
# A contended board produces numbers that look real and are not: the first screening
# pass was lost to another app plus an Edge Impulse runner holding ~1.8 of the 4 cores,
# and 1b Q4_0 reported 1.0 tok/s against 5.3 on a quiet board — a 5.3x error that only
# showed up as an implausibly low figure. Both T1 (tok/s) and T2 (p90 latency) depend on
# this, so it lives in one file rather than in whichever harness remembered to check.
# Set BENCH_ALLOW_BUSY=1 to measure deliberately under load.
require_quiet() {
  [ "${BENCH_ALLOW_BUSY:-0}" = 1 ] && return 0
  # Only name-check processes that can look idle at this instant and still peg a core
  # during the run. The old list also matched 'main\.py' and 'etx_runF', which belonged
  # to the other App Lab app that used to share this board; it no longer exists, and
  # 'main\.py' now matches Daybreak's own python/main.py — i.e. it would abort every
  # benchmark once the app runs. The CPU check below is the real contention guard.
  local p
  for p in 'edge-impulse-linux-runner'; do
    if pgrep -f "$p" >/dev/null 2>&1; then
      echo "ABORT: '$p' is running — board not quiet" >&2; pgrep -af "$p" >&2; exit 1
    fi
  done
  # /proc/stat over 3 s, not loadavg: loadavg decays with a ~1 min half-life, so just
  # after stopping a heavy process it still reads high on a board that is already idle.
  local u1 n1 s1 i1 w1 q1 sq1 u2 n2 s2 i2 w2 q2 sq2 busy
  read -r _ u1 n1 s1 i1 w1 q1 sq1 _ < /proc/stat
  sleep 3
  read -r _ u2 n2 s2 i2 w2 q2 sq2 _ < /proc/stat
  busy=$(awk -v a=$((u1+n1+s1+q1+sq1)) -v b=$((u1+n1+s1+i1+w1+q1+sq1)) \
             -v c=$((u2+n2+s2+q2+sq2)) -v d=$((u2+n2+s2+i2+w2+q2+sq2)) \
         'BEGIN{t=d-b; if(t<=0)t=1; printf "%.1f", 100*(c-a)/t}')
  if awk -v x="$busy" 'BEGIN{exit !(x>15)}'; then
    echo "ABORT: CPU $busy% busy before the run" >&2
    ps -eo pcpu,args --sort=-pcpu --no-headers | head -5 >&2
    exit 1
  fi
  echo "board quiet: ${busy}% cpu, $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) MB available" >&2
}

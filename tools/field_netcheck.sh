#!/usr/bin/env bash
# Field network self-test — run at the venue (or Friday, simulating it) to confirm the
# workstation has internet via the phone tether WITHOUT breaking the 10.0.0.0/24 orb network,
# so a remote Claude Code session can reach the API to fix things. Read-only; suggests the fix.
set -u
ok(){ printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad(){ printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$1"; }

echo "=== interfaces (up, with IPv4) ==="
ip -brief -4 addr | awk '$2=="UP"||$3!=""{print "  "$0}'

echo "=== default routes (lowest metric wins) ==="
ip route | grep '^default' | sed 's/^/  /'

echo "=== checks ==="
# 1. orb network present on eth0
if ip route | grep -q '10.0.0.0/24 dev eth0'; then ok "orb network 10.0.0.0/24 on eth0"; else bad "10.0.0.0/24 NOT on eth0 — orbs won't be reachable"; fi

# 2. AP reachable
if ping -c1 -W1 10.0.0.1 >/dev/null 2>&1; then ok "AP 10.0.0.1 reachable"; else warn "AP 10.0.0.1 not reachable (AP off / cable?)"; fi

# 3. which interface owns the winning default route
DEFIF=$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'dev [a-z0-9]+' | head -1 | awk '{print $2}')
echo "  internet-bound traffic exits via: ${DEFIF:-none}"
if [ "${DEFIF:-}" = "eth0" ]; then
  warn "default route is eth0 (the AP) — at the venue this is a DEAD END for internet"
  warn "FIX:  sudo ip route del default via 10.0.0.1 dev eth0   # let the tether win"
fi

# 4. actual internet + API reachability (the thing that matters for remote fixes)
if curl -sI -m 6 https://api.anthropic.com/ >/dev/null 2>&1; then ok "api.anthropic.com reachable (remote fixes possible)"; else bad "api.anthropic.com UNREACHABLE — apply the FIX above, re-tether, or check phone data"; fi
if curl -sI -m 6 https://github.com >/dev/null 2>&1; then ok "github reachable"; else warn "github unreachable"; fi

echo "=== verdict ==="
if curl -sI -m 6 https://api.anthropic.com/ >/dev/null 2>&1 && ip route | grep -q '10.0.0.0/24 dev eth0'; then
  printf '  \033[32mGREEN — internet up + orb network intact. Good to go.\033[0m\n'
else
  printf '  \033[31mNOT READY — see failed checks above.\033[0m\n'
fi

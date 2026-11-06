#!/usr/bin/env bash
# F1c: private CA + server certificate for the FilmLab cockpit (run inside the FilmLab distro).
#
#   f1c-tls.sh            create the CA (once) and a server cert (if missing)
#   f1c-tls.sh --renew    reissue only the server cert (new IP/names, or expiry); the CA stays
#   f1c-tls.sh --force-ca replace the CA too (every device must then trust the new CA)
#
# Keys never leave ~/.config/filmlab/tls (mode 700/600). The public CA cert is also copied to
# <repo>\local\filmlab-ca.crt (git-ignored) for installing on devices.
#
# The PC's LAN address, subnet and name come from the installer (FILMLAB_LAN_IP, FILMLAB_LAN_CIDR, FILMLAB_HOSTNAME);
# without them they are detected (with mirrored networking the distro sees the PC's own addresses).
set -euo pipefail

DIR="$HOME/.config/filmlab/tls"
EXPORT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/local"
LAN_IP="${FILMLAB_LAN_IP:-$(hostname -I | tr ' ' '\n' | grep -E '^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | head -1)}"
[[ -n "$LAN_IP" ]] || { echo "couldn't find this PC's LAN address; set FILMLAB_LAN_IP" >&2; exit 1; }
LAN_CIDR="${FILMLAB_LAN_CIDR:-${LAN_IP%.*}.0/24}"
HOST="${FILMLAB_HOSTNAME:-$(hostname)}"; HOST="${HOST,,}"
cidr_mask() {  # 192.168.1.0/24 -> 192.168.1.0/255.255.255.0 (what nameConstraints wants)
  local bits=${1#*/} m="" i
  for i in 1 2 3 4; do
    if (( bits >= 8 )); then m+=255; bits=$((bits - 8)); else m+=$(( 256 - (1 << (8 - bits)) )); bits=0; fi
    (( i < 4 )) && m+=.
  done
  echo "${1%/*}/$m"
}
LAN_NET="$(cidr_mask "$LAN_CIDR")"
SAN_IPS=("$LAN_IP" 127.0.0.1)
SAN_DNS=(localhost "$HOST" "$HOST.local")
LEAF_DAYS=397          # within the Apple/Chrome 398-day limit
CA_DAYS=3650

mode="${1:-}"
umask 077
mkdir -p "$DIR"; chmod 700 "$DIR"
cd "$DIR"

if [[ -f ca.key && "$mode" == "--force-ca" ]]; then
  ts=$(date +%Y%m%d%H%M%S); mkdir -p old; mv ca.key "old/ca.key.$ts"; mv ca.crt "old/ca.crt.$ts"
  rm -f server.key server.crt
fi

if [[ ! -f ca.key ]]; then
  echo "creating FilmLab Studio Local CA"
  # Name constraints: this CA can only vouch for the home subnet, loopback and this host's names.
  cat > ca.cnf <<EOF
[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca
[dn]
CN = FilmLab Studio Local CA
O = FilmLab Studio (${HOST^^})
[v3_ca]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical, permitted;IP:${LAN_NET}, permitted;IP:127.0.0.0/255.0.0.0, permitted;DNS:localhost, permitted;DNS:${HOST}, permitted;DNS:${HOST}.local
EOF
  openssl ecparam -name prime256v1 -genkey -noout -out ca.key
  openssl req -new -x509 -key ca.key -sha256 -days "$CA_DAYS" -config ca.cnf -out ca.crt
  rm -f server.key server.crt
fi

if [[ ! -f server.crt || "$mode" == "--renew" ]]; then
  echo "issuing server certificate"
  {
    echo "[req]"; echo "distinguished_name = dn"; echo "prompt = no"
    echo "[dn]"; echo "CN = $HOST"
    echo "[v3]"
    echo "basicConstraints = critical, CA:FALSE"
    echo "keyUsage = critical, digitalSignature"
    echo "extendedKeyUsage = serverAuth"
    echo "subjectKeyIdentifier = hash"
    echo "authorityKeyIdentifier = keyid"
    echo "subjectAltName = @san"
    echo "[san]"
    i=1; for ip in "${SAN_IPS[@]}"; do echo "IP.$i = $ip"; i=$((i+1)); done
    i=1; for d in "${SAN_DNS[@]}"; do echo "DNS.$i = $d"; i=$((i+1)); done
  } > server.cnf
  openssl ecparam -name prime256v1 -genkey -noout -out server.key.new
  openssl req -new -key server.key.new -config server.cnf -out server.csr
  openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -sha256 \
    -days "$LEAF_DAYS" -extfile server.cnf -extensions v3 -out server.crt.new
  openssl verify -CAfile ca.crt server.crt.new >/dev/null
  mv server.key.new server.key; mv server.crt.new server.crt; rm -f server.csr
fi

chmod 600 ./*.key ./*.cnf 2>/dev/null || true
chmod 644 ca.crt server.crt

mkdir -p "$EXPORT"
cp ca.crt "$EXPORT/filmlab-ca.crt"

echo
echo "CA:     $DIR/ca.crt  (copy for devices: $EXPORT/filmlab-ca.crt)"
echo "        $(openssl x509 -in ca.crt -noout -fingerprint -sha256)"
echo "Server: $DIR/server.crt, valid until $(openssl x509 -in server.crt -noout -enddate | cut -d= -f2)"
openssl x509 -in server.crt -noout -ext subjectAltName | tail -n +2 | sed 's/^ */        /'

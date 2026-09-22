# Panduan Testing Manual — ZTNA / SDN Testbed

Semua perintah untuk dijalankan **manual di Linux (VM1 & VM2)**.

- **VM1 `sdn1` = `192.168.56.2`** — OpenDaylight + PDP + analisis/perf.
- **VM2 `sdn2` = `192.168.56.3`** — Mininet / Open vSwitch (data plane).

> Aturan emas: **ODL harus Running sebelum PDP dan Mininet.** Kalau ODL mati,
> provisioning akan gagal (`Connection refused`) dan flow = 0 walaupun tier benar.

---

## 0. Ringkasan terminal

| Terminal | VM | Isi |
|---|---|---|
| 1 | VM1 | OpenDaylight (cukup start sekali, jalan di background) |
| 2 | VM1 | PDP (`pdp.py`) |
| 3 | VM2 | Mininet (`ztna_net.py`) → prompt `mininet>` |
| 4 (opsional) | VM1 | curl / `perf_collect` / `sensitivity_analysis` |

---

## 1. VM1 — OpenDaylight

```bash
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ODL=~/karaf-0.23.1

# cek status
$ODL/bin/status

# jalankan (butuh ~60 detik sampai siap)
$ODL/bin/start

# tunggu, lalu cek lagi sampai "Running ..." dan port listen
$ODL/bin/status
ss -tln | grep -E '8181|6653|8101'

# uji RESTCONF
curl -s -u admin:admin -H 'Accept: application/yang-data+json' \
  'http://localhost:8181/rests/data/network-topology:network-topology?content=nonconfig' \
  | head -c 300; echo

# stop
$ODL/bin/stop

# log
tail -f $ODL/data/log/karaf.log
```

---

## 2. VM1 — PDP (`pdp.py`)

```bash
cd ~/PDP

# lihat opsi
python3 pdp.py --help

# jalankan normal
sudo python3 pdp.py
# atau background:
sudo nohup python3 pdp.py >/tmp/pdp.log 2>&1 &
tail -f /tmp/pdp.log

# variasi bobot (sesuai permintaan reviewer)
sudo python3 pdp.py --w-r 0.45 --w-c 0.25 --w-b 0.30

# variasi penalti
sudo python3 pdp.py --penalty-fail 30 --penalty-mac 40

# validasi (harus GAGAL sebelum menyentuh ODL)
python3 pdp.py --dry-run --w-r 0.4 --w-c 0.4 --w-b 0.4     # jumlah != 1.0
python3 pdp.py --dry-run --w-r 1.5 --w-c -0.5 --w-b 0.0    # di luar [0,1]

# sehatkan / metrik
curl -s http://localhost:5000/health; echo
curl -s http://localhost:5000/metrics | python3 -m json.tool

# stop PDP
pkill -f 'python3 pdp.py'
```

---

## 3. VM2 — Mininet (`ztna_net.py`)

```bash
cd ~/mini-projects

# pastikan bersih dulu
sudo mn -c

# jalankan (tunggu pesan "Ready")
sudo python3 ztna_net.py
```

Setelah muncul `mininet>`, lanjut ke bagian 4.

---

## 4. Mininet CLI — client manual

```text
# cek konektivitas dasar
mininet> h1 ping -c1 10.0.0.2

# login interaktif
mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py --username ratih
#   password: research123

# login non-interaktif + probe resource
mininet> h1 bash -lc 'printf "%s\n" "research123" | python3 /home/ubuntu/mini-projects/pep_client.py --username ratih --password-stdin --probe'

# login dengan MAC dipalsukan (memicu LIMITED)
mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py --username ratih --mac aa:bb:cc:dd:ee:ff

# login user guest (memicu DENIED)
mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py --username bima

# logout (pakai token dari output login)
mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py --logout <TOKEN>

# lihat flow yang terpasang di OVS
mininet> sh sudo ovs-ofctl -O OpenFlow13 dump-flows s1

# keluar dari Mininet
mininet> exit
```

---

## 5. VM1 — skenario deterministik via curl

Menghasilkan tier FULL / LIMITED / DENIED dengan kontrol penuh (tidak perlu Mininet).

```bash
PDP=http://localhost:5000

# --- FULL: R=80, C=100, B=100 -> T=90 ---
curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"ratih","password":"research123","ip":"10.0.0.1","mac":"00:00:00:00:00:01"}'
echo

# --- LIMITED: 2 gagal login (B=70) + MAC mismatch (C=50) -> T=69 ---
for i in 1 2; do
  curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
   -d '{"username":"ratih","password":"x"}' >/dev/null
done
curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"ratih","password":"research123","ip":"10.0.0.1","mac":"aa:bb:cc:dd:ee:ff"}'
echo

# --- DENIED: guest + 4 gagal login (B=40) + MAC mismatch -> T=38 ---
for i in 1 2 3 4; do
  curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
   -d '{"username":"bima","password":"x"}' >/dev/null
done
curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"bima","password":"guest123","ip":"10.0.0.4","mac":"aa:bb:cc:dd:ee:ff"}'
echo
```

> Login gagal dihitung per-user dan **tidak di-reset** sampai PDP di-restart.
> Karena itu urutannya: FULL dulu, baru LIMITED, baru DENIED.

---

## 6. VM1 — pengukuran performa otomatis (`perf_collect.py`)

Pastikan **ODL + PDP + Mininet** hidup.

```bash
cd ~/PDP
python3 perf_collect.py --cycles 20 --mass
python3 perf_collect.py --cycles 30
cat perf_results.md
cat perf_results.csv
```

---

## 7. VM1 — sensitivity analysis (tanpa testbed)

```bash
cd ~/PDP
python3 sensitivity_analysis.py
cat sensitivity_results.md

# gambar (opsional, butuh matplotlib)
sudo apt-get install -y python3-matplotlib
python3 sensitivity_analysis.py --figure
```

---

## 8. VM1 — Dashboard Visualizer (opsional)

Jika `visualizer/app.py` sudah ada di VM1:

```bash
cd ~/visualizer
python3 app.py
# buka di browser: http://192.168.56.2:5000
```

---

## 9. Troubleshooting

```bash
# ODL belum siap -> PUT gagal "Connection refused"
ss -tln | grep 8181
tail -50 ~/karaf-0.23.1/data/log/karaf.log

# lihat error provision
tail -50 /tmp/pdp.log

# port terpakai / proses nyangkut
ss -tlnp | grep -E '5000|8181|6653'
pkill -f 'python3 pdp.py'
pkill -f 'python3 ztna_net.py'

# bersihkan Mininet
sudo mn -c

# lihat flow yang benar-benar terpasang
#   VM2:
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
#   VM1 (config datastore ODL):
curl -s -u admin:admin -H 'Accept: application/yang-data+json' \
 'http://localhost:8181/rests/data/opendaylight-inventory:nodes?content=nonconfig' | head -c 400; echo

# token/sesi aktif
curl -s http://localhost:5000/metrics | python3 -m json.tool
```

---

## 10. Urutan lengkap satu sesi uji (dari nol)

```bash
# --- VM1 ---
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
~/karaf-0.23.1/bin/start
# tunggu ~60s, cek:
ss -tln | grep 8181

cd ~/PDP && sudo nohup python3 pdp.py >/tmp/pdp.log 2>&1 &

# --- VM2 ---
cd ~/mini-projects && sudo mn -c && sudo python3 ztna_net.py
# tunggu "Ready", lalu di mininet>:
#   h1 bash -lc 'printf "%s\n" "research123" | python3 /home/ubuntu/mini-projects/pep_client.py --username ratih --password-stdin --probe'

# --- VM1 (terminal lain) ---
cd ~/PDP && python3 perf_collect.py --cycles 20 --mass

# --- bersih-bersih ---
# VM2: mininet> exit
pkill -f 'python3 pdp.py'      # VM1
# ~/karaf-0.23.1/bin/stop       # VM1 (opsional)
```

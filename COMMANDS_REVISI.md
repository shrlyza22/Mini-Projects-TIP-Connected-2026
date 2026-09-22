# Playbook Reproduksi Revisi Paper (ICCEREC 2026)

Kumpulan **perintah lengkap** untuk menjalankan ulang seluruh pekerjaan revisi
yang sudah dilakukan: deploy file, sensitivity analysis, performance, dan uji
skenario.

- **VM1 `sdn1` = `192.168.56.2`** — OpenDaylight + PDP + analisis/perf.
- **VM2 `sdn2` = `192.168.56.3`** — Mininet / OVS.
- **Laptop (Windows)** — repo sumber `D:\Telkom University\Mini-Projects`.

> Urutan penting: **ODL Running → PDP → (Mininet) → ukur**.
> Kalau ODL mati, PUT flow gagal (`Connection refused`) dan flow = 0.

---

## 0. Posisi file (hasil deploy)

| File | VM | Path di VM |
|---|---|---|
| `pdp.py` | VM1 | `~/PDP/pdp.py` |
| `sensitivity_analysis.py` | VM1 | `~/PDP/sensitivity_analysis.py` |
| `perf_collect.py` | VM1 | `~/PDP/perf_collect.py` |
| `pep_client.py` | VM2 | `~/mini-projects/pep_client.py` |
| `ztna_net.py` | VM2 | `~/mini-projects/ztna_net.py` |

---

## 1. (Opsional) Deploy ulang dari laptop ke VM

Jalankan dari **PowerShell laptop**, bila repo berubah:

```powershell
# ke VM1
scp "D:\Telkom University\Mini-Projects\vm-controller\PDP\pdp.py"                  192.168.56.2:~/PDP/pdp.py
scp "D:\Telkom University\Mini-Projects\vm-controller\PDP\sensitivity_analysis.py" 192.168.56.2:~/PDP/sensitivity_analysis.py
scp "D:\Telkom University\Mini-Projects\vm-controller\PDP\perf_collect.py"         192.168.56.2:~/PDP/perf_collect.py

# ke VM2
scp "D:\Telkom University\Mini-Projects\vm-mininet\PEP\pep_client.py" 192.168.56.3:~/mini-projects/pep_client.py
scp "D:\Telkom University\Mini-Projects\vm-mininet\PEP\ztna_net.py"   192.168.56.3:~/mini-projects/ztna_net.py
```

Cek sintaks di VM:

```bash
# VM1
cd ~/PDP && python3 -m py_compile pdp.py sensitivity_analysis.py perf_collect.py && echo OK
# VM2
cd ~/mini-projects && python3 -m py_compile pep_client.py ztna_net.py && echo OK
```

---

## 2. VM1 — OpenDaylight

```bash
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ODL=~/karaf-0.23.1

$ODL/bin/status                 # cek
$ODL/bin/start                  # jalankan (tunggu ~60 detik)

# pastikan siap sebelum lanjut
$ODL/bin/status                 # harus "Running ..."
ss -tln | grep -E '8181|6653|8101'

# uji RESTCONF
curl -s -u admin:admin -H 'Accept: application/yang-data+json' \
  'http://localhost:8181/rests/data/network-topology:network-topology?content=nonconfig' | head -c 250; echo
```

---

## 3. VM1 — Sensitivity analysis (tanpa testbed)

```bash
cd ~/PDP
python3 sensitivity_analysis.py
cat sensitivity_results.md
cat sensitivity_results.csv

# gambar (opsional)
sudo apt-get install -y python3-matplotlib
python3 sensitivity_analysis.py --figure
```

---

## 4. VM1 — PDP

```bash
cd ~/PDP
python3 pdp.py --help

# validasi (harus GAGAL sebelum menyentuh ODL)
python3 pdp.py --dry-run --w-r 0.4 --w-c 0.4 --w-b 0.4
python3 pdp.py --dry-run --w-r 1.5 --w-c -0.5 --w-b 0.0

# jalankan (background)
sudo nohup python3 pdp.py >/tmp/pdp.log 2>&1 &
sleep 8
tail -n 20 /tmp/pdp.log

# cek sehat
curl -s http://localhost:5000/health; echo
curl -s http://localhost:5000/metrics | python3 -m json.tool
```

Variasi konfigurasi (opsional):

```bash
sudo python3 pdp.py --w-r 0.45 --w-c 0.25 --w-b 0.30
sudo python3 pdp.py --penalty-fail 30 --penalty-mac 40
```

---

## 5. VM1 — Performance collection (angka R3-4)

> Catatan: dengan hanya ODL+PDP hidup, angka mengukur **latency PDP + load
> controller**. Untuk mencakup data plane, jalankan juga Mininet (bagian 6)
> sebelum langkah ini.

```bash
cd ~/PDP
python3 perf_collect.py --cycles 20 --mass
cat perf_results.md
```

Hasil acuan: provisioning mean ~143 ms, revocation mean ~94 ms, revocation
bersamaan 4 sesi / 64 flow / ~248 ms, CPU ODL ~140%.

---

## 6. VM2 — Mininet (untuk uji end-to-end & data plane)

```bash
cd ~/mini-projects
sudo mn -c
sudo python3 ztna_net.py         # tunggu pesan "Ready"
```

Di prompt `mininet>`:

```text
mininet> h1 ping -c1 10.0.0.2
mininet> h1 bash -lc 'printf "%s\n" "research123" | python3 /home/ubuntu/mini-projects/pep_client.py --username ratih --password-stdin --probe'
mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py --username ratih --mac aa:bb:cc:dd:ee:ff
mininet> sh sudo ovs-ofctl -O OpenFlow13 dump-flows s1
mininet> exit
```

> Setelah Mininet hidup, ulangi bagian 5 (`perf_collect.py`) untuk angka yang
> sudah mencakup switch.

---

## 7. VM1 — Skenario deterministik via curl

```bash
PDP=http://localhost:5000

# FULL -> T=90
curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"ratih","password":"research123","ip":"10.0.0.1","mac":"00:00:00:00:00:01"}'; echo

# LIMITED -> T=69 (2 gagal login + MAC mismatch)
for i in 1 2; do curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"ratih","password":"x"}' >/dev/null; done
curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"ratih","password":"research123","ip":"10.0.0.1","mac":"aa:bb:cc:dd:ee:ff"}'; echo

# DENIED -> T=38 (guest + 4 gagal login + MAC mismatch)
for i in 1 2 3 4; do curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"bima","password":"x"}' >/dev/null; done
curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"bima","password":"guest123","ip":"10.0.0.4","mac":"aa:bb:cc:dd:ee:ff"}'; echo
```

---

## 8. Pemeriksaan & verifikasi (yang dijalankan saat revisi)

```bash
# sintaks semua skrip (VM1)
cd ~/PDP && python3 -m py_compile pdp.py sensitivity_analysis.py perf_collect.py && echo OK

# flag PDP terpasang
python3 pdp.py --help

# validasi bobot & batas
python3 pdp.py --dry-run --w-r 0.4 --w-c 0.4 --w-b 0.4     # jumlah != 1.0
python3 pdp.py --dry-run --w-r 1.5 --w-c -0.5 --w-b 0.0    # di luar [0,1]

# uji /metrics tanpa server (import)
python3 -c "import pdp; c=pdp.app.test_client(); pdp.FLOW_PUT_MS.extend([3.5,5.0,8.2]); pdp.METRICS['logins']=2; print(c.get('/metrics').get_json())"

# sensitivity di VM
python3 sensitivity_analysis.py | tail -8

# help klien (VM2)
cd ~/mini-projects && python3 pep_client.py --help
```

---

## 9. Bersih-bersih

```bash
# VM2
#   mininet> exit
sudo mn -c

# VM1
pkill -f 'python3 pdp.py'
pkill -f 'python3 perf_collect.py'
# ~/karaf-0.23.1/bin/stop      # opsional
```

---

## 10. Urutan satu tarikan penuh (ringkas)

```bash
# VM1
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
~/karaf-0.23.1/bin/start && sleep 60 && ss -tln | grep 8181
cd ~/PDP && sudo nohup python3 pdp.py >/tmp/pdp.log 2>&1 & sleep 8
python3 sensitivity_analysis.py
python3 perf_collect.py --cycles 20 --mass
cat perf_results.md

# VM2 (opsional, untuk data plane)
cd ~/mini-projects && sudo mn -c && sudo python3 ztna_net.py
```

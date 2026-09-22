# Laporan Sesi Revisi ZTNA/SDN — ICCEREC 2026

Dokumen ini merangkum **semua yang dikerjakan, hasil angka, perintah, dan penjelasan konfigurasi** pada sesi revisi testbed Zero Trust (ZTNA) berbasis OpenDaylight. Ditulis agar mudah dipahami dan bisa langsung dijadikan bahan naskah maupun dokumentasi.

---

## 1. Ringkasan Singkat

Pada sesi ini kami:

1. Menjalankan **sensitivity analysis** untuk menjawab kritik reviewer soal bobot & threshold trust score.
2. Mengukur **performa** (latency, beban controller, throughput) untuk melengkapi hasil yang tadinya hanya "functional".
3. Menemukan dan memperbaiki **4 masalah nyata** pada testbed.
4. Membuktikan **diskriminasi tier** (FULL/LIMITED/DENIED) benar-benar bekerja di data plane.

**Kesimpulan utama:** mekanisme trust-aware access control berjalan end-to-end — dari keputusan trust di PDP sampai penegakan aturan OpenFlow di OVS — dengan angka performa terukur dan hasil yang konsisten dengan desain.

---

## 2. Arsitektur & Skenario

```
VM1 (sdn1, 192.168.56.2)          VM2 (sdn2, 192.168.56.3)
┌────────────────────────┐        ┌──────────────────────────────┐
│ OpenDaylight (Karaf)   │◄──OF──►│ Mininet: ring s1-s2-s3-s4     │
│ PDP (Flask, :5000)     │◄─REST─►│ h1 research 10.0.0.1          │
└────────────────────────┘        │ h2 server   10.0.0.2          │
                                   │ h3 iot      10.0.0.3          │
                                   │ h4 guest    10.0.0.4          │
                                   │ nat0 gateway 10.0.0.254       │
                                   └──────────────────────────────┘
```

- **PDP** menghitung trust score `T = 0.5R + 0.3C + 0.2B`, menentukan tier, lalu memasang/menghapus flow lewat RESTCONF ke OpenDaylight.
- **OpenDaylight** menerapkan flow ke Open vSwitch (OpenFlow 1.3).
- **Default-deny**: semua akses tertutup sampai ada sesi terautentikasi.

**Tier akses:**

| Tier | Ambang | Akses |
|---|---|---|
| FULL | T ≥ 70 | server (8080, 9000, 22) + IoT (80) |
| LIMITED | 40 ≤ T < 70 | hanya IoT (80) |
| DENIED | T < 40 | tidak ada |

---

## 3. Hasil Angka

### 3.1 Trust score (skenario uji)

| Tier | Akun | R | C | B | T |
|---|---|---|---|---|---|
| FULL | ratih (research) | 80 | 100 | 100 | **90** |
| LIMITED | ratih (research) | 80 | 70* | 40 | **69** |
| DENIED | bima (guest) | 30 | 50 | 40 | **38** |

\* Pada uji LIMITED terakhir, penalti konteks dipicu lewat **jam akses** (bukan MAC spoof), sehingga flow terikat ke MAC asli dan bisa dipakai host.

### 3.2 Sensitivity analysis (bobot & threshold)

Bobot alternatif yang disebut reviewer **(0.45, 0.25, 0.30)** menghasilkan **91 / 69.5 / 38** — **pemetaan tier tetap sama**.

| Konfigurasi | wR | wC | wB | FULL | LIMITED | DENIED |
|---|---|---|---|---|---|---|
| Baseline | 0.50 | 0.30 | 0.20 | 90.0 (FULL) | 69.0 (LIMITED) | 38.0 (DENIED) |
| Alt-1 | 0.45 | 0.25 | 0.30 | 91.0 (FULL) | 69.5 (LIMITED) | 38.0 (DENIED) |
| Alt-2 | 0.40 | 0.40 | 0.20 | 92.0 (FULL) | 66.0 (LIMITED) | 40.0 → LIMITED |
| Alt-3 | 0.40 | 0.20 | 0.40 | 92.0 (FULL) | 70.0 → FULL | 38.0 (DENIED) |
| Alt-4 | 0.60 | 0.30 | 0.10 | 88.0 (FULL) | 70.0 → FULL | 37.0 (DENIED) |
| Alt-5 | 0.33 | 0.33 | 0.33 | 93.3 (FULL) | 66.7 (LIMITED) | 40.0 → LIMITED |

**Threshold:** 75/45 dan 70/50 mempertahankan pemetaan; 65/35 dan 60/30 mengubahnya.

**Gap nilai:** tidak ada kombinasi valid yang menghasilkan tepat 70 atau 40. Threshold 70 berada di celah `(69.0, 70.5]`, dan 40 di `(39.0, 41.0]` — jadi tahan pergeseran kecil.

**Robustness:** 11.7% (seluruh simplex), 51.3% (domain `wR≥wC≥wB`), 84.0% (±0.05 dari baseline).

### 3.3 Performa

| Metrik | Nilai (rentang antar-run) |
|---|---|
| Provisioning (login) | mean **101–144 ms**, p95 145–197 ms |
| Revocation (logout) | mean **67–94 ms**, p95 98–138 ms |
| RESTCONF PUT per rule | avg **6.3–7.8 ms**, p95 ~10–13 ms |
| Revocation bersamaan | 4 sesi, **64 flow**, 248–349 ms |
| CPU OpenDaylight saat churn | **116–149%** |
| Throughput OVS (FULL, port 9000) | **71.0 Gbit/s** sender / 70.6 receiver (82.7 GB / 10 s) |
| Loopback h2 (pembanding) | ~85 Gbit/s |

> Angka bervariasi antar-run (JVM warm-up, beban mesin). Untuk naskah, pilih **satu prosedur tetap** dan laporkan run itu saja.

### 3.4 Diskriminasi tier di data plane

| Keadaan | server:9000 | server:8080 | IoT:80 |
|---|---|---|---|
| Default-deny (pra-login) | diblok | diblok | diblok |
| FULL | **71 Gbit/s** | HTTP 200 | HTTP 200 |
| LIMITED | diblok | diblok | **HTTP 200** |
| Post-logout | diblok | diblok | diblok |

Ini membuktikan kebijakan adaptif bekerja nyata di data plane, bukan hanya di level keputusan.

---

## 4. Masalah yang Ditemukan & Diperbaiki

| # | Masalah | Gejala | Perbaikan | Efek |
|---|---|---|---|---|
| 1 | Carveout PDP (prio 200) bentrok dengan default-deny (prio 200) | login dari host `timed out` | carveout dinaikkan ke **prio 300** | login host berhasil |
| 2 | Port 9000 ditempati `http.server`, bukan `iperf3` | `Bad file descriptor` | matikan http.server dulu | iperf valid |
| 3 | Layout port ring `ztna_net.py` tidak cocok dengan `pdp.py`/paper | arah balik h2→h1 salah port → h1↔h2 putus | layout diselaraskan + `FORWARD_NEXT_HOP`/`RETURN_HOPS` | h1↔h2 tembus (71 Gbit/s) |
| 4 | Spoof MAC untuk memicu LIMITED mengikat flow ke MAC palsu | LIMITED tercapai tapi host tak bisa pakai akses | tambah opsi `--allowed-hours` | LIMITED tercapai dengan MAC asli → data plane bisa dipakai |

**Pelajaran:** konsistensi mapping port antar-komponen (PDP ↔ topologi ↔ OVS) itu kritis; kesalahan kecil di situ membuat trafik hilang tanpa error jelas.

---

## 5. Penjelasan Konfigurasi

### 5.1 Opsi PDP (`pdp.py`)

| Opsi | Fungsi | Contoh |
|---|---|---|
| `--w-r/--w-c/--w-b` | Bobot trust (harus berjumlah 1.0) | `--w-r 0.45 --w-c 0.25 --w-b 0.30` |
| `--full-threshold` | Ambang FULL | `--full-threshold 75` |
| `--limited-threshold` | Ambang LIMITED | `--limited-threshold 45` |
| `--penalty-ip` | Penalti IP di luar subnet | `--penalty-ip 40` |
| `--penalty-hours` | Penalti di luar jam akses | `--penalty-hours 30` |
| `--penalty-mac` | Penalti IP/MAC tidak cocok | `--penalty-mac 50` |
| `--penalty-fail` | Penalti per gagal login | `--penalty-fail 15` |
| `--penalty-fail-cap` | Batas maksimum penalti perilaku | `--penalty-fail-cap 60` |
| `--allowed-hours` | Rentang jam akses `START-END` | `--allowed-hours 1-1` (selalu di luar jam → memicu LIMITED tanpa spoof MAC) |
| `--dry-run` | Cetak JSON RESTCONF tanpa memanggil ODL | `--dry-run` |
| `--port` | Port Flask | `--port 5000` |

Endpoint tambahan: `GET /metrics` (latency PUT, jumlah login/logout/flow), `GET /health`.

### 5.2 Perubahan `ztna_net.py`

| Perubahan | Nilai | Alasan |
|---|---|---|
| `CARVEOUT_PRIORITY` | 300 | agar di atas default-deny (200) dan session (250) |
| Layout ring | `s1:2↔s2:2`, `s2:3↔s3:2`, `s3:3↔s4:2`, `s4:3↔s1:3` | cocok dengan `pdp.py` & paper |
| `FORWARD_NEXT_HOP` | s2→2, s3→2, s4→3 | menuju s1 (PDP) |
| `RETURN_HOPS` | s2→s3 = port 3 | arah balik ke h3 |

### 5.3 `perf_collect.py`

- `--cycles N` : jumlah siklus login/logout untuk mengukur distribusi latency.
- `--mass` : uji revocation bersamaan (4 sesi) → beban controller.
- `--odl-pid` : PID Karaf untuk sampling CPU (auto-deteksi).
- Output: `perf_results.md`, `perf_results.csv`.

### 5.4 `sensitivity_analysis.py`

- Menghitung `T` untuk berbagai konfigurasi bobot & threshold (tanpa testbed).
- Output: tabel, analisis gap, robustness, `sensitivity_results.md/.csv`.
- `--figure` : gambar heatmap (butuh matplotlib).

---

## 6. Semua Command yang Dipakai

> Versi lengkap: lihat `PANDUAN_TESTING.md` dan `COMMANDS_REVISI.md`.

### 6.1 VM1 — OpenDaylight

```bash
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
~/karaf-0.23.1/bin/start        # tunggu ~60-90s
~/karaf-0.23.1/bin/status       # harus "Running ..."
ss -tln | grep 8181
```

### 6.2 VM1 — PDP

```bash
cd ~/PDP
nohup python3 pdp.py >/tmp/pdp.log 2>&1 &
sleep 8 && curl -s http://localhost:5000/health; echo
curl -s http://localhost:5000/metrics | python3 -m json.tool
```

Mode khusus (uji LIMITED tanpa spoof MAC):

```bash
pkill -f 'python3 pdp.py'; sleep 2
cd ~/PDP && nohup python3 pdp.py --allowed-hours 1-1 >/tmp/pdp.log 2>&1 &
```

### 6.3 VM1 — Sensitivity & Performance

```bash
cd ~/PDP
python3 sensitivity_analysis.py
python3 perf_collect.py --cycles 20 --mass
cat perf_results.md
```

### 6.4 VM2 — Mininet

```bash
cd ~/mini-projects
sudo mn -c
sudo python3 ztna_net.py        # tunggu "Ready"
```

### 6.5 Mininet CLI — client & uji

```text
mininet> h1 ping -c3 192.168.56.2
mininet> h1 bash -lc 'printf "%s\n" "research123" | python3 /home/ubuntu/mini-projects/pep_client.py --username ratih --password-stdin'
mininet> h1 iperf3 -c 10.0.0.2 -p 9000 -t 10
mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py --logout <TOKEN>
```

### 6.6 Throughput & diskriminasi tier

```text
mininet> h2 pkill -f iperf3
mininet> h2 iperf3 -s -p 9000 -D
mininet> h1 iperf3 -c 10.0.0.2 -p 9000 -t 10
```

### 6.7 Skenario deterministik via curl (VM1)

```bash
PDP=http://localhost:5000
# FULL -> T=90
curl -s -X POST $PDP/login -H 'Content-Type: application/json' \
 -d '{"username":"ratih","password":"research123","ip":"10.0.0.1","mac":"00:00:00:00:00:01"}'; echo
```

---

## 7. Efek & Hasil untuk Testbed

| Aspek | Sebelum | Sesudah perbaikan |
|---|---|---|
| Login host ke PDP | `timed out` | berhasil (GRANTED) |
| h1 ↔ h2 (data plane) | putus | tembus, **71 Gbit/s** |
| LIMITED di data plane | tak teruji | server diblok, IoT 200 |
| Reaksi default-deny | ada tapi bocor | konsisten (pra-login & post-logout diblok) |
| Reversibilitas | — | logout mencabut flow (67–94 ms) |

**Implikasi untuk paper:** klaim "adaptif" dan "enforcement" kini didukung bukti data-plane terukur, bukan sekadar fungsional. Keempat temuan bug juga layak disebut sebagai catatan implementasi (menunjukkan ketelitian).

---

## 8. Langkah Selanjutnya

1. **Tulis ke naskah:**
   - Subsection Performance (latency + throughput + diskriminasi tier).
   - Paragraf justifikasi **ML/AI** (kenapa bukan LSTM/CNN).
   - Paragraf justifikasi **topologi** (topology-agnostic + future work).
   - Rapikan robustness (cantumkan grid 0.05 & jumlah sampel).
   - Referensi bobot/threshold + proofread.
2. **Canonical run**: satu tarikan bersih untuk angka final.
3. **Administrasi**: camera-ready, copyright, registrasi (cek `iccerec.com`).

---

## 9. Kesimpulan

- **Eksperimen:** selesai untuk semua yang feasible (sensitivity + performance + diskriminasi tier).
- **Naskah:** sebagian besar tersisa menulis.
- **Arsitektur:** tetap utuh; diperkuat dengan justifikasi, bukan diganti.
- **Kualitas:** solid — ada data nyata, analisis sensitivitas, batasan yang jujur, dan perbaikan bug yang terdokumentasi.

*Terakhir diperbarui: sesi revisi ICCEREC 2026.*

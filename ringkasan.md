# Ringkasan Project — SDN & ZTNA Testbed + QKD

Repositori ini berisi kumpulan mini-project eksperimen **Software-Defined Networking (SDN)**, **Zero Trust Network Access (ZTNA)**, dan **Quantum Key Distribution (BB84)** dari Telkom University (TIP Connected 2026).

---

## 1. Gambaran Umum

| Aspek | Keterangan |
| :--- | :--- |
| **Tujuan** | Testbed SDN + ZTNA dengan penegakan kebijakan berbasis trust score |
| **SDN Controller** | OpenDaylight (ODL) — `192.168.56.2:8181` (RESTCONF), `:6653` (OpenFlow) |
| **Data Plane** | Mininet + Open vSwitch, topologi *ring* 4 switch |
| **Bahasa** | Python 3.8+ |
| **Library** | Flask, requests, qiskit, qiskit-aer, numpy, matplotlib |

---

## 2. Arsitektur Dua VM

```text
VM1 — Controller & Policy Engine
├── OpenDaylight SDN Controller   (port 8181 / 6653)
├── ZTNA PDP Engine               (Flask, port 5000)
└── ODL Dashboard Visualizer      (Flask, port 5000)

VM2 — Data Plane (Mininet)
└── OVS Ring: s1 - s2 - s3 - s4
    ├── h1 Research  10.0.0.1  ─┐
    ├── h2 Server    10.0.0.2   │ 1 host per switch (port 1)
    ├── h3 IoT       10.0.0.3   │
    ├── h4 Guest     10.0.0.4  ─┘
    └── nat0 Gateway 10.0.0.254 (di s1, hanya mode PEP)
```

**Alur satu sesi:**
1. Client (`pep_client.py`) login HTTP ke PDP `/login`.
2. PDP autentikasi + hitung Trust Score.
3. PDP pasang flow OpenFlow via RESTCONF ke ODL.
4. ODL install flow ke OVS (OpenFlow 1.3).
5. Traffic data antar-host diizinkan sesuai kebijakan.

---

## 3. Struktur Direktori

```text
Mini-Projects/
├── visualizer/                # Dashboard topologi & flow ODL
│   ├── app.py
│   └── README.md
├── vm-controller/
│   └── PDP/                   # ZTNA Policy Decision Point (VM1)
│       ├── pdp.py
│       └── README.md
├── vm-mininet/
│   ├── PEP/                   # Data plane ZTNA (VM2)
│   │   ├── pep_client.py
│   │   ├── ztna_net.py
│   │   └── README.md
│   └── topologi/              # Topologi ring SDN standar
│       ├── ring-topo.py
│       └── README.md
├── qkd_pembanding/            # Simulasi BB84
│   ├── bb84_sim.py
│   ├── bb84_analysis.py
│   └── bb84_qber_kgr.png
└── ringkasan.md               # Dokumen ini
```

---

## 4. Modul & Fungsi Utama

### 4.1 Visualizer Dashboard (`visualizer/app.py`)
- Pengganti OpenDaylight DLUX; berjalan di VM1 port 5000.
- **Endpoint REST:**
  - `GET /api/topology` — node switch/host + edge (layout generik di frontend, tidak hardcode 4 switch).
  - `GET /api/flows?store=operational|config` — tabel flow tiap switch; verdict `allow` / `drop` / `punt`.
  - `GET /api/test-connection` — cek status + latency ke ODL.
- Auto-refresh 5 detik; topology tiap 15 detik.
- Mendeteksi status host-tracker untuk membedakan mode L2Switch vs ZTNA.

### 4.2 ZTNA Policy Decision Point (`vm-controller/PDP/pdp.py`)
- Flask endpoint: `POST /login`, `POST /logout`, `GET /health`.
- **Trust Score:** `T = w_R·R + w_C·C + w_B·B` dengan `w_R=0.5, w_C=0.3, w_B=0.2`.
- **Tier akses:**
  | Tier | Ambang | Hak Akses |
  | :--- | :--- | :--- |
  | FULL | T ≥ 70 | semua port terotorisasi |
  | LIMITED | 40 ≤ T < 70 | hanya port non-sensitif (`80`, `8080`) |
  | DENIED | T < 40 | diblokir total |
- **Flow yang dipasang:**
  - `PRIO_DEFAULT_DENY=200` — default-deny persisten per host tujuan.
  - `PRIO_SESSION=250` — allow per sesi (dua arah) sepanjang shortest path ring.
  - Carveout ke portal PDP.
- Path dihitung BFS di atas ring; return leg = path forward dibalik (routing simetris).
- Menyediakan `--dry-run` (cetak JSON RESTCONF tanpa memanggil ODL) dan override threshold.

### 4.3 PEP Data Plane (`vm-mininet/PEP/`)
- **`ztna_net.py`** — topologi ring 4 switch + NAT, static ARP (tanpa broadcast loop), fail-mode `secure`, default-drop prio 0.
  - LLDP-punt (`prio 100`) & PDP carveout (`prio 200`) dipush via **RESTCONF** agar bertahan saat OpenFlow resync (flow via dpctl akan terhapus).
  - Carveout mencakup semua host h1–h4, dengan return flow per (switch, host).
- **`pep_client.py`** — client login/logout; membaca IP & MAC host sendiri, mendukung `--password-stdin`, `--logout`, `--debug`.

### 4.4 Topologi Standar (`vm-mininet/topologi/ring-topo.py`)
- Mode default: hanya 4 host + 4 switch ring (tanpa NAT).
- `--pep`: tambah `nat0` + flow infrastruktur PEP (cookie `0xfeed`, priority `310`).
- Warm-up ARP host-discovery + verifikasi mapping port (`verify_ring_ports`).

### 4.5 QKD BB84 (`qkd_pembanding/`)
- **`bb84_sim.py`** — simulasi BB84 penuh: prepare → channel (opsional Eve intercept-resend) → measure → sifting → estimasi QBER → shared key. Output: QBER, KGR, panjang key, perbandingan tanpa vs dengan Eve.
- **`bb84_analysis.py`** — variasi intensitas eavesdropping (0/25/50/75/100%), 5 trial per titik, plot QBER & KGR vs Eve → `bb84_qber_kgr.png`.
- Ambang aman BB84 ~11% (Shor-Preskill bound).

---

## 5. Pemetaan Host & Segment

| Host | IP | MAC | Segment | Switch |
| :--- | :--- | :--- | :--- | :--- |
| h1 | 10.0.0.1 | 00:00:00:00:00:01 | Research | s1 (Port 1) |
| h2 | 10.0.0.2 | 00:00:00:00:00:02 | Server | s2 (Port 1) |
| h3 | 10.0.0.3 | 00:00:00:00:00:03 | IoT | s3 (Port 1) |
| h4 | 10.0.0.4 | 00:00:00:00:00:04 | Guest | s4 (Port 1) |

**Kebijakan segmentasi (POLICY):**
- `research` → server `[8080, 9000, 22]`, iot `[80]`
- `iot` → server `[9000]`
- `guest` → (terisolasi, tanpa akses)
- `server` → (tidak menginisiasi)

**User:** `ratih/research123` (research), `bima/guest123` (guest).

---

## 6. Cara Menjalankan

**Skenario Standar SDN:**
```bash
# VM2
cd vm-mininet/topologi
sudo python3 ring-topo.py
```

**Skenario ZTNA:**
```bash
# VM1 terlebih dahulu
cd vm-controller/PDP
sudo python3 pdp.py            # atau --dry-run

# VM2
cd vm-mininet/PEP
sudo python3 ztna_net.py

# Di Mininet CLI
mininet> h1 python3 pep_client.py
```

**Dashboard Visualizer:**
```bash
cd visualizer
python3 app.py                 # http://<IP-VM1>:5000
```

**Simulasi QKD:**
```bash
cd qkd_pembanding
python bb84_sim.py
python bb84_analysis.py
```

---

## 7. Poin Teknis Penting

- **Flow via RESTCONF vs dpctl:** flow yang dipasang langsung ke OVS (dpctl/ovs-ofctl) tidak terlihat ODL dan terhapus saat resync. Karena itu LLDP-punt & carveout dipush ke config datastore ODL via RESTCONF agar persisten.
- **Anti-spoof:** match flow sesi menyertakan `ethernet-source` (MAC) + IPv4 src/dst spesifik.
- **Routing simetris:** return leg memakai path forward yang dibalik.
- **Loop-free:** static ARP menghindari broadcast ARP di topologi ring.
- **Fail-mode secure:** switch men-drop paket tak match meski flow default-drop hilang.

# 🔒 ZTNA Data Plane & PEP Client

Direktori ini berisi komponen **Policy Enforcement Point (PEP)** dan topologi *data-plane* ZTNA yang dijalankan di **VM2** (Mininet & Open vSwitch).

---

## 📄 Komponen File

### 1. `ztna_net.py` (ZTNA Data Plane Topology)
Membangun topologi *ring* 4 Open vSwitch (`s1` – `s4`) dengan 4 *host* tersegregasi dan 1 NAT Gateway:
- **`h1`** (`10.0.0.1` / Research) terhubung ke `s1`
- **`h2`** (`10.0.0.2` / Server) terhubung ke `s2`
- **`h3`** (`10.0.0.3` / IoT) terhubung ke `s3`
- **`h4`** (`10.0.0.4` / Guest) terhubung ke `s4`
- **NAT Gateway** (`10.0.0.254`) terpasang pada `s1` untuk meneruskan lalu lintas HTTP autentikasi dari namespace host ke PDP di VM1 (`192.168.56.2:5000`).

#### Keunggulan Penanganan Flow:
- Memasang aturan **Default-Drop** (*microsegmentation*).
- Menyuntikkan aturan **LLDP Punt** ($priority=100$) dan **PDP Carveout** ($priority=200$) langsung ke **ODL Config Datastore** via RESTCONF agar flow tidak hilang saat OpenFlow *resynchronization*.

### 2. `pep_client.py` (ZTNA CLI Client)
Skrip CLI ringan (menggunakan Python *stdlib* tanpa dependensi `pip`) yang dijalankan di dalam namespace *host* Mininet untuk melakukan proses *login* ke PDP.

---

## 💻 Cara Menjalankan Skenario ZTNA

### Langkah 1: Jalankan Topologi ZTNA (di VM2)
```bash
cd vm-mininet/PEP
sudo python3 ztna_net.py
```
*Skrip ini akan mengonfigurasi OVS, memasang NAT gateway, dan membuka prompt `mininet>`*.

### Langkah 2: Autentikasi Host (Dari Prompt Mininet)
Untuk melakukan login dari host tertentu (contoh: `h1`):

```bash
mininet> h1 python3 pep_client.py
```

1. Masukkan **username** (contoh: `alice`).
2. Masukkan **password** (contoh: `research123`).
3. Client akan membaca IP & MAC lokal `h1`, mengirim request ke PDP (`http://192.168.56.2:5000/login`), dan menampikan status Trust Score serta daftar akses yang diberikan.

### Langkah 3: Uji Konektivitas Data Plane
Setelah login berhasil, uji konektivitas antar-host:
```bash
# Uji ping atau curl port terotorisasi dari h1 ke h2
mininet> h1 ping -c 2 10.0.0.2
```

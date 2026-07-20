# 🌐 ODL Dashboard & Topology Visualizer (v4)

Dashboard web interaktif berbasis **Flask** yang berfungsi sebagai pengganti OpenDaylight DLUX untuk memantau topologi jaringan SDN dan menginspeksi tabel *flow* OpenFlow secara real-time.

---

## 🚀 Fitur Utama

- **Visualisasi Topologi Ring Generik**: Algoritma `computeLayout()` di frontend menyusun switch dalam bentuk *ring* dinamis dan menata letak *host* terhubung secara proporsional.
- **Flow Inspector Real-Time**: Mengambil data inventaris switch dan aturan *flow* aktif langsung dari OpenDaylight via **RESTCONF API**.
- **Dukungan Mode Ganda**:
  - **Mode L2Switch Standar**: Menampilkan penemuan *host* otomatis (*host-tracker* aktif).
  - **Mode ZTNA**: Menyesuaikan tampilan untuk pengujian ZTNA (*host-tracker* nonaktif karena `odl-l2switch-switch` di-uninstall, *flow* dipasang oleh PDP).

---

## 🛠️ Persyaratan & Instalasi

Modul ini dijalankan pada **VM1** (tempat OpenDaylight berjalan).

```bash
# Instalasi dependensi Python
pip install flask requests
```

---

## ⚙️ Konfigurasi

Konfigurasi IP dan port OpenDaylight berada di bagian atas [`app.py`](file:///d:/Telkom%20University/Mini-Projects/visualizer/app.py):

```python
ODL_IP = "192.168.56.2"  # IP VM OpenDaylight
ODL_PORT = 8181
ODL_USER = "admin"
ODL_PASS = "admin"
```

---

## 💻 Cara Menjalankan

```bash
cd visualizer
python3 app.py
```

Setelah berjalan, buka browser di alamat:
👉 **`http://<IP-VM1>:5000`** atau **`http://localhost:5000`**

---

## 📡 Endpoint RESTCONF yang Digunakan

Dashboard melakukan *query* ke endpoint OpenDaylight berikut:
- **Topology**: `GET /rests/data/network-topology:network-topology`
- **Inventory & Flows**: `GET /rests/data/opendaylight-inventory:nodes`

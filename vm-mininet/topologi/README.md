# 🌐 Topologi Ring SDN Standar (L2Switch Testbed)

Direktori ini berisi skrip topologi Mininet untuk pengujian fitur **SDN standar** menggunakan pengontrol OpenDaylight dengan fitur `odl-l2switch-switch` yang aktif.

---

## 📄 Deskripsi Skrip (`ring-topo.py`)

- **Topologi**: 4 Open vSwitch (`s1`, `s2`, `s3`, `s4`) terhubung dalam struktur *ring* tertutup (`s1-s2`, `s2-s3`, `s3-s4`, `s4-s1`).
- **Host**: 4 host (`h1` s/d `h4`), masing-masing terhubung ke port 1 pada switch terkait.
- **Automated ARP Warm-up**: Memiliki fungsi `warm-up()` bawaan untuk membersihkan tabel ARP cache dan mengirimkan paket ping awal antar-host (`h1`-`h2`, `h2`-`h3`, `h3`-`h4`, `h4`-`h1`).
  - Hal ini dilakukan agar OpenDaylight *Host-Tracker* dapat langsung mengenali lokasi seluruh host dan menampilkannya pada visualizer dashboard.

---

## 💻 Cara Menjalankan

Modul ini dijalankan pada **VM2** (Mininet):

```bash
cd vm-mininet/topologi
sudo python3 ring-topo.py
```

Setelah topologi terbentuk dan proses *warm-up* selesai, prompt Mininet (`mininet>`) akan aktif. Anda dapat melakukan pengujian ping manual:

```bash
mininet> pingall
```

# 🛡️ ZTNA Policy Decision Point (PDP) Engine

Modul ini merupakan komponen **Policy Decision Point (PDP)** dalam arsitektur Zero Trust Network Access (ZTNA). Berjalan di **VM1** berdampingan dengan OpenDaylight SDN Controller.

---

## 🎯 Fungsi & Peran PDP

1. **Autentikasi & Identifikasi**: Menerima permintaan autentikasi dari *PEP Client* via HTTP POST `/login`.
2. **Kalkulasi Trust Score ($T$)**: Menghitung skor kepercayaan dinamis berdasarkan identitas pengguna, segmen jaringan, dan konteks:
   
   $$T(s, t) = w_R \cdot R + w_C \cdot C + w_B \cdot B$$
   
   - $w_R = 0.5$ (Identitas Role)
   - $w_C = 0.3$ (Konteks Perangkat & Waktu)
   - $w_B = 0.2$ (Perilaku / Baseline)

3. **Evaluasi Kebijakan & Tier**:
   - **Full Tier ($\ge 70$)**: Mengizinkan semua port terotorisasi untuk peran tersebut.
   - **Limited Tier ($40 - 69$)**: Membatasi ke port non-sensitif (port `80`, `8080`).
   - **Denied ($< 40$)**: Memblokir seluruh akses.
4. **Flow Provisioning via RESTCONF**: Menghitung jalur terpendek pada topologi *ring* dan menyuntikkan aturan *flow* OpenFlow 1.3 secara langsung ke **Config Datastore** OpenDaylight.

---

## 🔑 Data Pengguna & Kebijakan Segmen

### Pengguna Terdaftar (`USERS`)
- `alice` / `research123` ➔ Role: **Research** (Skor Dasar $R = 80$)
- `bob` / `guest123` ➔ Role: **Guest** (Skor Dasar $R = 30$)

### Matriks Akses Kebijakan (`POLICY`)
- **Research**: Berhak mengakses segmen **Server** (port `8080`, `9000`, `22`) dan **IoT** (port `80`).
- **IoT**: Berhak mengakses segmen **Server** (port `9000`).
- **Guest / Server**: Tidak diizinkan membuat koneksi keluar baru (Fully Isolated).

---

## 💻 Cara Menjalankan

### Mode Produksi (Interaksi Langsung dengan OpenDaylight)
```bash
cd vm-controller/PDP
pip install flask requests
sudo python3 pdp.py
```
*Port default: `5000` (Listening pada `0.0.0.0:5000`)*.

### Mode Simulasi / Dry-Run (Tanpa Mengubah Flow ODL)
Gunakan opsi `--dry-run` untuk melihat hasil kalkulasi Trust Score dan format payload JSON RESTCONF tanpa mengirimkannya ke ODL:
```bash
python3 pdp.py --dry-run
```

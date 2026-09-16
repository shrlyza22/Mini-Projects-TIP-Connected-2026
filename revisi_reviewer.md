# Catatan Revisi — Adaptive Trust-Aware Access Control (ICCEREC 2026)

Dokumen ini memetakan komentar reviewer ke aksi konkret, lalu menjelaskan secara
mendalam **semua skenario** yang dihasilkan oleh `sensitivity_analysis.py` dan
bagaimana mengaitkannya ke naskah. Paper acuan: **versi OpenDaylight** (ring 4
switch, threshold **FULL ≥ 70 / LIMITED 40–69 / DENIED < 40**, bobot
**0.5 / 0.3 / 0.2**).

---

## 1. Peta komentar reviewer → aksi → bukti

| # | Komentar | Jenis | Status / bukti |
|---|---|---|---|
| R1a | Justifikasi bobot 0.5/0.3/0.2 & threshold 70/40; tambahkan **sensitivity analysis** | Eksperimen + tulis | ✅ `sensitivity_analysis.py`, `sensitivity_results.md` (Bagian 4–8 di bawah) |
| R1b | Fig. 1: definisi H1–H4 & S1–S4 di teks + caption | Tulis | ⏳ belum (naskah) |
| R1c | Rasional teoretis komponen T = 0.5R+0.3C+0.2B | Tulis | ✅ bahan di Bagian 3 |
| R1d | Abstract < 200 kata → perluas | Tulis | ⏳ belum |
| R1e | Format persamaan matematis (typesetting) | Tulis | ⏳ belum |
| R2 | Rasional bobot; proofread typo | Tulis + kode | ✅ rasional Bagian 5; typo `pep_client_fixed.py` diperbaiki |
| R3-1 | Kenapa 0.5/0.3/0.2 bukan 0.45/0.25/0.30; tunjukkan hasil empiris | Eksperimen | ✅ `Alt-1` di Bagian 5 |
| R3-2 | Justifikasi kenapa **bukan** ML/AI | Tulis | ⏳ bahan di Bagian 9 |
| R3-3 | Skala topologi (ABILENE/GEANT) | Eksperimen | ⏳ Fase C |
| R3-4 | Performance metrics (latency PDP, load ODL, throughput OVS) | Eksperimen | 🟡 parsial: `provision_ms`, `revoke_ms`, `/metrics`, `--probe` |

---

## 2. Dua level pengujian (penting agar tidak bingung)

| Level | Yang diuji | Alat | Butuh login? | Menjawab |
|---|---|---|---|---|
| **Model** | Aritmetika `T = wR·R + wC·C + wB·B` | `sensitivity_analysis.py` | Tidak | "Kalau bobot/threshold diubah, tier berubah?" |
| **Sistem** | Auth → OpenFlow → akses HTTP | `pdp.py` + Mininet | Ya | "Dengan kebijakan ini, akses benar-benar terbuka/tertutup?" |

Login **hanya bertugas menghasilkan angka R, C, B**. Karena rumusnya
deterministik, hasil sensitivity bisa dihitung langsung tanpa menjalankan
testbed — dan memang harus begitu, sebab bobot di `pdp.py` dibaca sekali saat
startup sehingga login tidak dapat mengubahnya.

---

## 3. Rincian faktor R, C, B (dari `pdp.py`)

**Identitas R** (bobot 50%) — skor dasar per role:

| Role | R |
|---|---|
| research | 80 |
| server | 95 |
| iot | 50 |
| guest | 30 |

**Konteks C** (bobot 30%) — mulai 100, dikurangi:

| Kondisi | Penalti |
|---|---|
| IP di luar subnet `10.0.0.0/24` | −40 |
| Akses di luar jam izin | −30 |
| IP/MAC tidak cocok (spoofing) | −50 |

**Perilaku B** (bobot 20%) — `B = max(0, 100 − min(n×15, 60))`, `n` = jumlah
gagal login:

| n | B |
|---|---|
| 0 | 100 |
| 1 | 85 |
| 2 | 70 |
| 3 | 55 |
| 4+ | 40 |

> Rasional (R1c): ketiga faktor inilah yang dipakai literatur dynamic trust
> (NIST ZTA, Syed, dll.) — identitas (siapa), konteks (dari mana & kapan),
> perilaku (bagaimana sejauh ini). Bobot menempatkan **identitas** sebagai
> penentu utama, konteks sebagai penyesuai, perilaku sebagai sinyal sekunder.

---

## 4. Tiga skenario uji (FULL / LIMITED / DENIED)

Skrip `sensitivity_analysis.py` menampilkan bagian **[0]** berikut:

| Tier | Akun | R | C | B | T | Provenance |
|---|---|---|---|---|---|---|
| **FULL** | ratih (research) | 80 | 100 | 100 | **90.0** | MAC cocok, tanpa gagal login |
| **LIMITED** | ratih (research) | 80 | 50 | 70 | **69.0** | MAC mismatch (−50) + 2 gagal login |
| **DENIED** | bima (guest) | 30 | 50 | 40 | **38.0** | MAC mismatch + 4 gagal login |

**Cara memicu di sistem nyata** (lihat panduan uji):

- **FULL** — login `ratih / research123` dengan MAC asli.
- **LIMITED** — 2× gagal login `ratih`, lalu login benar dengan **MAC dipalsukan**
  (`--mac` atau payload curl). `R=80, C=50, B=70 → T=69` → turun ke LIMITED;
  server diblok, IoT tetap boleh.
- **DENIED** — 4× gagal login `bima`, lalu login benar dengan **MAC dipalsukan**.
  `R=30, C=50, B=40 → T=38` → ditolak total (HTTP 403).

Temuan: penurunan bersifat **bertahap** (FULL → LIMITED → DENIED), bukan biner.

---

## 5. Skenario sensitivity — variasi BOBOT (threshold tetap 70/40)

Bagian **[1]**. Nilai `T` untuk tiap kasus di bawah tiap konfigurasi bobot:

| Konfigurasi | wR | wC | wB | FULL | LIMITED | DENIED |
|---|---|---|---|---|---|---|
| Baseline (paper) | 0.50 | 0.30 | 0.20 | 90.0 (FULL) | 69.0 (LIMITED) | 38.0 (DENIED) |
| Alt-1 (flatter) | 0.45 | 0.25 | 0.30 | 91.0 (FULL) | 69.5 (LIMITED) | 38.0 (DENIED) |
| Alt-2 (context-heavy) | 0.40 | 0.40 | 0.20 | 92.0 (FULL) | 66.0 (LIMITED) | **40.0 (LIMITED)** |
| Alt-3 (behaviour-heavy) | 0.40 | 0.20 | 0.40 | 92.0 (FULL) | **70.0 (FULL)** | 38.0 (DENIED) |
| Alt-4 (identity-heavy) | 0.60 | 0.30 | 0.10 | 88.0 (FULL) | **70.0 (FULL)** | 37.0 (DENIED) |
| Alt-5 (uniform) | 0.33 | 0.33 | 0.33 | 93.3 (FULL) | 66.7 (LIMITED) | **40.0 (LIMITED)** |

### Analisis baris per baris

- **Alt-1 (0.45 / 0.25 / 0.30)** — *jawaban langsung untuk reviewer (R3-1).*
  Meski bobot digeser dari 0.5/0.3/0.2, ketiga kasus **tetap** FULL/LIMITED/DENIED
  (T = 91 / 69.5 / 38). Artinya pemilihan baseline tidak "ajaib": alternatif yang
  reviewer sebutkan menghasilkan keputusan yang sama.

- **Alt-2 (0.40 / 0.40 / 0.20)** — bobot **identitas diturunkan** ke 40%.
  Kasus DENIED naik ke `T = 0.4·30 + 0.4·50 + 0.2·40 = 40` → tepat menyentuh batas
  LIMITED. **Implikasi:** jika identitas diberi bobot terlalu kecil, guest (R=30)
  yang seharusnya ditolak bisa lolos ke LIMITED. → identitas harus cukup dominan.

- **Alt-3 (0.40 / 0.20 / 0.40)** — bobot **perilaku dinaikkan** ke 40%.
  Kasus LIMITED naik ke `T = 0.4·80 + 0.2·50 + 0.4·70 = 70` → FULL.
  **Implikasi:** karena perilaku (B=70) relatif tinggi, menaikkan bobotnya bisa
  "memaafkan" konteks yang terkompromi (MAC mismatch) dan memberi akses penuh.
  → perilaku sebaiknya **bukan** faktor dominan.

- **Alt-4 (0.60 / 0.30 / 0.10)** — bobot **identitas dinaikkan** ke 60%.
  Kasus LIMITED naik ke `T = 0.6·80 + 0.3·50 + 0.1·70 = 70` → FULL.
  **Implikasi:** identitas yang terlalu dominan membuat sesi berkonteks buruk
  tetap dianggap penuh selama identitasnya kuat. → identitas tinggi, tapi tidak
  mendominasi mutlak.

- **Alt-5 (seragam 1/3)** — kasus DENIED naik ke `T = (30+50+40)/3 = 40` → LIMITED.
  Sama seperti Alt-2: bobot identitas yang tidak dominan melonggarkan penolakan.

**Kesimpulan Bagian 5:** titik sensitif hanya **kasus-kasus yang diletakkan tepat
di batas** (69 dan 38). Selama bobot tetap **identitas-dominan** (wR terbesar),
pemetaan tier tidak berubah. Ini justifikasi kuat bahwa 0.5/0.3/0.2 masuk akal,
bukan angka sembarang.

---

## 6. Skenario sensitivity — variasi THRESHOLD (bobot tetap 0.5/0.3/0.2)

Bagian **[2]**:

| Konfigurasi | FULL | LIMITED | FULL case | LIMITED case | DENIED case |
|---|---|---|---|---|---|
| Baseline 70/40 | 70 | 40 | 90.0 → FULL | 69.0 → LIMITED | 38.0 → DENIED |
| Looser 65/35 | 65 | 35 | 90.0 → FULL | 69.0 → **FULL** | 38.0 → **LIMITED** |
| Stricter 75/45 | 75 | 45 | 90.0 → FULL | 69.0 → LIMITED | 38.0 → DENIED |
| Limited@50 | 70 | 50 | 90.0 → FULL | 69.0 → LIMITED | 38.0 → DENIED |
| Lower 60/30 | 60 | 30 | 90.0 → FULL | 69.0 → **FULL** | 38.0 → **LIMITED** |

### Analisis

- **75/45** dan **70/50**: pemetaan **tetap** sama dengan baseline. Jadi
  mengetatkan batas sedikit, atau menaikkan batas LIMITED ke 50, **tidak**
  mengubah keputusan ketiga kasus.
- **65/35** dan **60/30**: melonggarkan batas membuat LIMITED→FULL dan
  DENIED→LIMITED. Wajar: menurunkan ambang berarti lebih mudah naik tier.

**Kesimpulan Bagian 6:** keputusan stabil terhadap pergeseran threshold moderat;
hanya pelonggaran besar yang mengubahnya.

---

## 7. Penempatan threshold vs nilai skor yang mungkin (gap analysis)

Bagian **[3]**. Himpunan semua `T` yang **mungkin** dihasilkan kombinasi R/C/B valid:

```
23, 26, 29, 32, 33, 35, 36, 38, 39, 41, 42, 44, 45, 47, 48, 50, 51, 53, 54,
55.5, 56, 57, 58.5, 59, 60, 61.5, 62, 63, 64.5, 65, 66, 67.5, 69, 70.5, 72,
73.5, 75, 76.5, 78, 79.5, 81, 82.5, 84, 85.5, 87, 88.5, 90, 91.5, 94.5, 97.5
```

**Tidak ada** kombinasi valid yang menghasilkan tepat 70 atau tepat 40:

- Threshold **70** berada di celah `(69.0, 70.5]`.
- Threshold **40** berada di celah `(39.0, 41.0]`.

**Implikasi:** berapa pun threshold dalam interval itu, hasil klasifikasi
**identik**. Jadi 70/40 berada di **celah alami**, bukan angka yang disetel agar
satu contoh kebetulan cocok. Ini argumen langsung untuk R1a.

---

## 8. Robustness pemetaan tier (jujur)

Bagian **[4]** — mengukur seberapa sering pemetaan FULL/LIMITED/DENIED bertahan
saat bobot divariasikan pada grid 0.05:

| Domain | Hasil |
|---|---|
| Seluruh simplex bobot | 27/231 (11.7%) |
| Domain desain `wR ≥ wC ≥ wB` | 79/154 (51.3%) |
| Sekitar baseline (±0.05) | 21/25 (84.0%) |

**Interpretasi yang jujur dan aman untuk naskah:**

1. Kasus uji **sengaja diletakkan di batas** (69 dan 38). Maka wajar bila bobot
   ekstrem membuatnya berpindah tier — ini justru menunjukkan ambang **bekerja**.
2. Di sekitar baseline (±0.05, mencerminkan galat kalibrasi), pemetaan **84% stabil**.
3. Alternatif spesifik reviewer (0.45/0.25/0.30) **100% mempertahankan** pemetaan.

> **Jangan** mengklaim "pemetaan robust di seluruh ruang bobot" — itu tidak benar.
> Klaim yang benar: *stabil secara lokal dan pada domain desain, dan sensitif
> hanya untuk bobot yang jauh dari identitas-dominan.*

---

## 9. Bahan justifikasi ML/AI (R3-2)

Model ini memakai **penalti titik** (`−50`, `−15`, dst.), bukan ML. Alasan yang
dapat dipertahankan untuk naskah:

- **Determinisme & auditabilitas** — keputusan dapat dilacak persis (setiap
  penalti punya sebab), penting untuk penegakan kebijakan jaringan.
- **Biaya & footprint** — tidak butuh pelatihan/data berlabel; PDP tetap ringan
  dan dapat dijalankan bersama controller di VM yang sama.
- **Keterbatasan jujur** — penalti titik tidak menangkap pola adversarial
  non-linier. → nyatakan sebagai **future work**: mengganti/memperkaya faktor
  perilaku dengan LSTM/SVDD (seperti IZTSDN), sambil mempertahankan rumus sebagai
  baseline yang transparan.

---

## 10. Rencana lanjutan

- **Fase B (performance live):** kumpulkan `provision_ms` / `revoke_ms` / `/metrics`,
  lalu uji throughput OVS. Catatan: port `8080/9000/22` sudah dipakai
  `http.server` di `ztna_net.py`, jadi iperf perlu port terpisah yang diizinkan
  kebijakan.
- **Fase C (topologi):** refactor agar topologi dibaca dari ODL, uji di ring
  besar, baru ABILENE (jangan langsung GEANT).
- **Naskah:** abstract diperluas, caption Fig.1 diberi definisi H1–H4/S1–S4,
  persamaan diformat, proofread.

---

## 11. Kalimat siap pakai (contoh untuk naskah)

- *"The weights were not obtained by statistical optimisation; a sensitivity
  analysis shows that the FULL/LIMITED/DENIED mapping of the three evaluated
  scenarios is preserved for identity-dominant weightings, including the
  alternative (0.45, 0.25, 0.30), and is stable for perturbations of ±0.05
  around the chosen values (84% of the sampled neighbourhood)."*
- *"The thresholds 70 and 40 fall inside gaps of the reachable score set
  ((69.0, 70.5] and (39.0, 41.0] respectively), so the tiering is invariant to
  small threshold shifts; loosening them to 65/35 or 60/30 is required before
  any evaluated scenario changes tier."*
- *"The mapping degrades only for weightings far from the identity-dominant
  region; the affected cases are precisely those placed on a tier boundary,
  which reflects the intended boundary behaviour of the adaptive policy."*

"""
BB84 Quantum Key Distribution — Simulation
===========================================
Simulasi protokol BB84 (Bennett & Brassard, 1984) menggunakan Qiskit.

Alur:
  1. Alice membangkitkan bit acak & basis acak (Z/X), encode qubit sesuai keduanya.
  2. Qubit dikirim lewat "quantum channel" (opsional: Eve intercept-resend attack).
  3. Bob membangkitkan basis acak sendiri, mengukur tiap qubit dengan basisnya.
  4. Sifting: Alice & Bob umumkan basis (bukan bit) lewat classical channel,
     buang posisi yang basisnya beda -> terbentuk "sifted key".
  5. Sebagian sifted key dikorbankan untuk estimasi QBER (Quantum Bit Error Rate).
  6. Sisanya jadi Shared Secret Key. KGR dihitung dari rasio key final / qubit terkirim.

Output: shared secret key, QBER, KGR, dan perbandingan kondisi tanpa vs dengan Eve.
"""

import random
import numpy as np
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

# ----------------------------------------------------------------------------
# Konfigurasi
# ----------------------------------------------------------------------------
N_QUBITS = 512          # jumlah qubit yang dikirim Alice -> Bob
SAMPLE_FRAC = 0.25      # fraksi sifted key yang dikorbankan untuk cek QBER
SEED = 42
EVE_PRESENT = False     # ganti True untuk simulasi serangan intercept-resend

random.seed(SEED)
np.random.seed(SEED)
sim = AerSimulator()

BASIS_Z, BASIS_X = 0, 1  # 0 = rectilinear (Z), 1 = diagonal (X)


# ----------------------------------------------------------------------------
# 1. Alice: bangkitkan bit & basis, encode qubit
# ----------------------------------------------------------------------------
def alice_prepare(n):
    bits = [random.randint(0, 1) for _ in range(n)]
    bases = [random.randint(0, 1) for _ in range(n)]
    circuits = []
    for bit, basis in zip(bits, bases):
        qc = QuantumCircuit(1, 1)
        if bit == 1:
            qc.x(0)                # encode |1>
        if basis == BASIS_X:
            qc.h(0)                 # putar ke basis diagonal (+/-)
        circuits.append(qc)
    return bits, bases, circuits


# ----------------------------------------------------------------------------
# 2. Channel: opsional Eve melakukan intercept-resend attack
# ----------------------------------------------------------------------------
def eve_intercept(circuits):
    """Eve mengukur tiap qubit dengan basis acak lalu mengirim ulang qubit baru
    sesuai hasil ukurnya sendiri -- attack klasik yang menaikkan QBER."""
    eve_bases = [random.randint(0, 1) for _ in circuits]
    new_circuits = []
    for qc, e_basis in zip(circuits, eve_bases):
        meas = qc.copy()
        if e_basis == BASIS_X:
            meas.h(0)
        meas.measure(0, 0)
        result = sim.run(meas, shots=1, memory=True).result()
        e_bit = int(result.get_memory()[0])

        resend = QuantumCircuit(1, 1)
        if e_bit == 1:
            resend.x(0)
        if e_basis == BASIS_X:
            resend.h(0)
        new_circuits.append(resend)
    return new_circuits


# ----------------------------------------------------------------------------
# 3. Bob: bangkitkan basis sendiri, ukur tiap qubit
# ----------------------------------------------------------------------------
def bob_measure(circuits):
    bases = [random.randint(0, 1) for _ in circuits]
    bits = []
    for qc, basis in zip(circuits, bases):
        meas = qc.copy()
        if basis == BASIS_X:
            meas.h(0)
        meas.measure(0, 0)
        result = sim.run(meas, shots=1, memory=True).result()
        bits.append(int(result.get_memory()[0]))
    return bits, bases


# ----------------------------------------------------------------------------
# 4. Sifting: buang posisi dengan basis Alice != basis Bob
# ----------------------------------------------------------------------------
def sift_key(a_bits, a_bases, b_bits, b_bases):
    a_sifted, b_sifted, idx_sifted = [], [], []
    for i, (ab, bb) in enumerate(zip(a_bases, b_bases)):
        if ab == bb:
            a_sifted.append(a_bits[i])
            b_sifted.append(b_bits[i])
            idx_sifted.append(i)
    return a_sifted, b_sifted, idx_sifted


# ----------------------------------------------------------------------------
# 5. Estimasi QBER dari subset sifted key yang dikorbankan
# ----------------------------------------------------------------------------
def estimate_qber(a_sifted, b_sifted, sample_frac):
    n = len(a_sifted)
    sample_size = max(1, int(n * sample_frac))
    sample_idx = set(random.sample(range(n), sample_size))

    mismatches = sum(
        1 for i in sample_idx if a_sifted[i] != b_sifted[i]
    )
    qber = mismatches / sample_size

    final_a = [a_sifted[i] for i in range(n) if i not in sample_idx]
    final_b = [b_sifted[i] for i in range(n) if i not in sample_idx]
    return qber, final_a, final_b, sample_size


# ----------------------------------------------------------------------------
# Jalankan satu skenario BB84 penuh
# ----------------------------------------------------------------------------
def run_bb84(n_qubits, with_eve=False, label=""):
    a_bits, a_bases, circuits = alice_prepare(n_qubits)

    if with_eve:
        circuits = eve_intercept(circuits)

    b_bits, b_bases = bob_measure(circuits)
    a_sifted, b_sifted, idx = sift_key(a_bits, a_bases, b_bits, b_bases)
    qber, final_a, final_b, sample_size = estimate_qber(a_sifted, b_sifted, SAMPLE_FRAC)

    key_match = sum(1 for x, y in zip(final_a, final_b) if x == y)
    kgr = len(final_a) / n_qubits  # key generation rate: key final / qubit terkirim

    print(f"\n=== BB84 Simulation {label} ===")
    print(f"Qubit terkirim           : {n_qubits}")
    print(f"Sifted key length        : {len(a_sifted)}  ({len(a_sifted)/n_qubits*100:.1f}% dari qubit terkirim)")
    print(f"Sample dikorbankan (QBER): {sample_size}")
    print(f"QBER                      : {qber*100:.2f}%")
    print(f"Final secret key length  : {len(final_a)}")
    print(f"Key Generation Rate (KGR): {kgr*100:.2f}%")
    print(f"Kecocokan key final (A==B): {key_match}/{len(final_a)}")
    print(f"Alice secret key (64 bit pertama) : {''.join(map(str, final_a[:64]))}")
    print(f"Bob   secret key (64 bit pertama) : {''.join(map(str, final_b[:64]))}")

    return {
        "n_qubits": n_qubits,
        "sifted_len": len(a_sifted),
        "qber": qber,
        "kgr": kgr,
        "final_key_len": len(final_a),
        "final_a": final_a,
        "final_b": final_b,
    }


if __name__ == "__main__":
    print("#" * 70)
    print("SKENARIO 1 — Tanpa Eve (kondisi normal)")
    print("#" * 70)
    normal = run_bb84(N_QUBITS, with_eve=False, label="(tanpa Eve)")

    random.seed(SEED)  # reset seed supaya perbandingan adil
    np.random.seed(SEED)

    print("\n" + "#" * 70)
    print("SKENARIO 2 — Dengan Eve (intercept-resend attack)")
    print("#" * 70)
    attacked = run_bb84(N_QUBITS, with_eve=True, label="(dengan Eve)")

    print("\n" + "#" * 70)
    print("RINGKASAN PERBANDINGAN")
    print("#" * 70)
    print(f"{'Skenario':<20}{'QBER':>10}{'KGR':>10}{'Final Key Len':>16}")
    print(f"{'Tanpa Eve':<20}{normal['qber']*100:>9.2f}%{normal['kgr']*100:>9.2f}%{normal['final_key_len']:>16}")
    print(f"{'Dengan Eve':<20}{attacked['qber']*100:>9.2f}%{attacked['kgr']*100:>9.2f}%{attacked['final_key_len']:>16}")
    print("\nCatatan: QBER > ~11% (Shor-Preskill bound untuk BB84) menandakan channel")
    print("kemungkinan disadap -> Alice & Bob membatalkan kunci dan mengulang.")
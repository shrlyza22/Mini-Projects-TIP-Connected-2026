"""
BB84 — Analisis KGR & QBER vs Intensitas Eavesdropping
=========================================================
Menjalankan simulasi BB84 berulang kali dengan variasi persentase qubit yang
disadap Eve (0%, 25%, 50%, 75%, 100%), lalu plot QBER & KGR untuk laporan.
"""

import random
import numpy as np
import matplotlib.pyplot as plt
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

N_QUBITS = 400
SAMPLE_FRAC = 0.25
TRIALS_PER_POINT = 5
EVE_LEVELS = [0, 25, 50, 75, 100]   # persen qubit yang disadap Eve
SEED_BASE = 100

sim = AerSimulator()
BASIS_Z, BASIS_X = 0, 1


def alice_prepare(n, rng):
    bits = [rng.randint(0, 1) for _ in range(n)]
    bases = [rng.randint(0, 1) for _ in range(n)]
    circuits = []
    for bit, basis in zip(bits, bases):
        qc = QuantumCircuit(1, 1)
        if bit == 1:
            qc.x(0)
        if basis == BASIS_X:
            qc.h(0)
        circuits.append(qc)
    return bits, bases, circuits


def eve_intercept_partial(circuits, eve_prob, rng):
    new_circuits = []
    for qc in circuits:
        if rng.random() * 100 >= eve_prob:
            new_circuits.append(qc)  # tidak disadap
            continue
        e_basis = rng.randint(0, 1)
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


def bob_measure(circuits, rng):
    bases = [rng.randint(0, 1) for _ in circuits]
    bits = []
    for qc, basis in zip(circuits, bases):
        meas = qc.copy()
        if basis == BASIS_X:
            meas.h(0)
        meas.measure(0, 0)
        result = sim.run(meas, shots=1, memory=True).result()
        bits.append(int(result.get_memory()[0]))
    return bits, bases


def sift_key(a_bits, a_bases, b_bits, b_bases):
    a_s, b_s = [], []
    for ab, bb, a, b in zip(a_bases, b_bases, a_bits, b_bits):
        if ab == bb:
            a_s.append(a)
            b_s.append(b)
    return a_s, b_s


def estimate_qber(a_sifted, b_sifted, frac, rng):
    n = len(a_sifted)
    if n == 0:
        return 0.0, 0
    sample_size = max(1, int(n * frac))
    idx = rng.sample(range(n), min(sample_size, n))
    mismatches = sum(1 for i in idx if a_sifted[i] != b_sifted[i])
    qber = mismatches / len(idx)
    remaining = n - len(idx)
    return qber, remaining


def one_trial(eve_prob, seed):
    rng = random.Random(seed)
    a_bits, a_bases, circuits = alice_prepare(N_QUBITS, rng)
    if eve_prob > 0:
        circuits = eve_intercept_partial(circuits, eve_prob, rng)
    b_bits, b_bases = bob_measure(circuits, rng)
    a_s, b_s = sift_key(a_bits, a_bases, b_bits, b_bases)
    qber, final_len = estimate_qber(a_s, b_s, SAMPLE_FRAC, rng)
    kgr = final_len / N_QUBITS
    return qber, kgr


results = {"eve_pct": [], "qber_mean": [], "qber_std": [], "kgr_mean": [], "kgr_std": []}
for lvl in EVE_LEVELS:
    qbers, kgrs = [], []
    for t in range(TRIALS_PER_POINT):
        q, k = one_trial(lvl, SEED_BASE + lvl * 10 + t)
        qbers.append(q)
        kgrs.append(k)
    results["eve_pct"].append(lvl)
    results["qber_mean"].append(np.mean(qbers) * 100)
    results["qber_std"].append(np.std(qbers) * 100)
    results["kgr_mean"].append(np.mean(kgrs) * 100)
    results["kgr_std"].append(np.std(kgrs) * 100)
    print(f"Eve {lvl:>3}% -> QBER {np.mean(qbers)*100:5.2f}% (\u00b1{np.std(qbers)*100:4.2f})   "
          f"KGR {np.mean(kgrs)*100:5.2f}% (\u00b1{np.std(kgrs)*100:4.2f})")

# ---------------------------------------------------------------- plot
fig, ax1 = plt.subplots(figsize=(7.5, 4.6), facecolor="#0B0E1A")
ax1.set_facecolor("#0B0E1A")

c_qber, c_kgr = "#E85C8A", "#5FB4E8"

ax1.errorbar(results["eve_pct"], results["qber_mean"], yerr=results["qber_std"],
             marker="o", color=c_qber, linewidth=2.2, capsize=4, label="QBER (%)")
ax1.axhline(11, color="#F2A65A", linestyle="--", linewidth=1.3, label="Ambang aman BB84 (~11%)")
ax1.set_xlabel("Persentase qubit disadap Eve (%)", color="white", fontsize=11)
ax1.set_ylabel("QBER (%)", color=c_qber, fontsize=11)
ax1.tick_params(colors="white")
for spine in ax1.spines.values():
    spine.set_color("#3A4560")

ax2 = ax1.twinx()
ax2.plot(results["eve_pct"], results["kgr_mean"], marker="s", color=c_kgr, linewidth=2.2, label="KGR (%)")
ax2.set_ylabel("Key Generation Rate (%)", color=c_kgr, fontsize=11)
ax2.tick_params(colors="white")

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
leg = ax1.legend(lines1 + lines2, labels1 + labels2, loc="center left", facecolor="#1B2338", edgecolor="none")
for text in leg.get_texts():
    text.set_color("white")

ax1.set_title(f"BB84 \u2014 QBER & KGR vs Intensitas Eavesdropping  (n={N_QUBITS} qubit/trial)",
              color="white", fontsize=12.5, pad=12)
ax1.grid(alpha=0.15, color="white")

plt.tight_layout()
plt.savefig("bb84_qber_kgr.png", dpi=160, facecolor=fig.get_facecolor())
print("\nSaved: bb84_qber_kgr.png")
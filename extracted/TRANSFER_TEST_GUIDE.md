# Ownership-Transfer Test Guide (v3.5.18)

How to test the sale scenario: **A sells land to B; B's papers match A's record
in every land detail (survey, khasra, village, area) and only the owner name
changed.** The system must recognize this as a transfer-to-verify, NOT a
conflict or a silent auto-approval.

## The 6 test documents (in `samples/`)

| File | What it is | Key details |
|---|---|---|
| `xfer_old_2019.png` | A's khatauni (the base record) | Owner **Ram Bahadur Singh**, survey **452**, khasra 77, village **Sundarpur**, area 2.5, year **2019-20** |
| `xfer_mutation_2021.png` | Mutation record A → B (the bridge) | "transferred from Ram Bahadur Singh to Kamla Devi Singh", survey 452, year **2021-22** |
| `xfer_new_2023.png` | B's khatauni (the transfer) | Owner **Kamla Devi Singh**, survey **452**, khasra 77, village **Sundarpur**, area 2.5, year **2023-24** |
| `xfer_conflict_2019.png` | C's khatauni — same year as A | Owner **Mahesh Verma**, survey 452, village Sundarpur, year **2019-20** (genuine conflict) |
| `xfer_other_village_2023.png` | Same survey number, DIFFERENT village | Owner Kamla Devi Singh, survey 452, village **Pipariya** (a different land) |
| `xfer_partition_2022.png` | B's sub-plot (partition sale) | Owner Kamla Devi Singh, survey 452, khasra **77/1**, area **1.25**, year 2022-23 |

> **Start on a fresh install (empty database) for exact results.** The test
> documents use survey 452 / village Sundarpur — if your database already has
> unrelated records with that combination, delete them first (Records tab →
> trash icon) or expect extra hints.

## Step-by-step (use the Upload tab's sample strip — one click each)

1. **Upload `xfer_old_2019.png`** (choose language: English)
   - Expected: **no flags**, status **Auto-Approved** (first record on this land).

2. **Upload `xfer_new_2023.png`** (the new owner's papers)
   - Expected: ⚠ hint **"Possible ownership transfer: same survey + village as
     record #… where the owner is 'Ram Bahadur Singh', but a different owner
     here — verify with the mutation/sale record before approving."**
   - Status: **Pending Verification** (never auto-approved)
   - Open the record → the **AI Decision Support** card shows
     **◉ REVIEW REQUIRED** with that flag.
   - It must NOT say "duplicate".

3. **Upload `xfer_mutation_2021.png`** and choose document type
   **Mutation Record (Namantaran / Ferfar)** in the upload form.

4. **Consistency Check tab** → tick `xfer_old_2019` + `xfer_new_2023` → Run
   - Expected flag: **⚠ WARNING** — "different owners in different record
     years (2019-20, 2023-24) in the same village — consistent with a
     legitimate sale/transfer between those years. Confirm with the mutation
     record." (NOT a red conflict.)

5. **Consistency Check** → tick `xfer_old_2019` + `xfer_new_2023` +
   `xfer_mutation_2021` → Run
   - Expected flag: **ℹ INFO** — "the set includes a mutation/sale document —
     this matches a recorded ownership transfer, not a conflict."

6. **Upload `xfer_conflict_2019.png`**
   - Expected: transfer hint fires (Mahesh Verma differs from the stored owner).

7. **Consistency Check** → tick `xfer_old_2019` + `xfer_conflict_2019` → Run
   - Expected flag: **⛔ ERROR** — "same survey in the same village shows
     different owners with the same (or no) record year — a genuine conflict,
     verify the chain of title." (Two people claiming the same land in the
     SAME year is never a transfer.)

8. **Upload `xfer_other_village_2023.png`**
   - Expected: **no transfer hint** (different village = different land).

9. **Consistency Check** → tick `xfer_new_2023` + `xfer_other_village_2023` → Run
   - Expected flag: **ℹ INFO** — "different villages … different lands that
     merely share a survey number, not a conflict."

10. **Upload `xfer_partition_2022.png`** (sub-khasra 77/1, half the area)
    - Expected: **possible-duplicate warning pointing at `xfer_new_2023`**
      (its parent record — same owner, same survey/village). A reviewer sees
      the khasra (77/1) and area (1.25) differ and confirms it is a partition,
      not a re-upload.

11. **Consistency Check** → tick `xfer_old_2019` + `xfer_partition_2022` → Run
    - Expected flag: **⚠ WARNING** (transfer pattern) **with the note
      "Area also differs (1.25 acre, 2.5 acre) — consistent with a partition."**

## Edge cases covered by the code (unit-tested, not UI-visible)

| Edge case | Behavior |
|---|---|
| OCR mangles the owner name slightly on a re-upload (e.g. "Ram Bahadur Sing") | Flagged as **possible duplicate** ("owner name differs only slightly — may be an OCR variation"), NOT as a transfer |
| Devanagari survey digits (४५ = 452) | Matched against ASCII "452" correctly |
| Survey "45/2" vs stored "452" | NOT matched (different numbers, same digit sequence) |
| Transfer candidate itself not yet verified | The hint notes "(status: pending_review — not yet verified itself)" |
| Multi-stage chain A→B→C, only A stored | C's hint points at the latest stored record (A) |
| Same owner, same land, re-upload | Classic duplicate check (exact owner match) — unchanged |

## Why this design

- A **transfer** (same land, new name) is a normal, expected event in land
  records — it must never be a red "conflict", and it must never be
  auto-approved silently. It gets a targeted hint and human confirmation.
- A **genuine conflict** (two different owners, same land, same year, no
  mutation) stays a red error.
- **Different lands that share a survey number** (different villages) are
  never flagged as conflicts.
- Everything is rule-based and local — no internet, no data leaves the PC.

# QGuard 開發與研究導讀

## 這個版本完成了什麼

這是一套可執行、可驗證、能重跑實驗的 Quantum EDA 研究原型。輸入已映射到
實體 qubit 的電路、閘時間、硬體連接與雜訊參數，輸出合法排程，並透過獨立
測試情境評估量子態 fidelity。

初版包含五種排程方法、NumPy 密度矩陣模擬器、Qiskit Aer 交叉驗證、原始數據
與作圖流程。mapping/routing 聯合最佳化、CP-SAT、DD 加速與真機驗證是後續工作，
目前的結果不代表已完成這些項目。

## 先親手跑一次

```bash
python -m pip install -e '.[dev,validation]'
python -m pytest -q
qguard example --family qaoa --qubits 4 --depth 1 --output results/problem.json
qguard schedule results/problem.json --method asap --simulate --output results/asap.json
qguard schedule results/problem.json --method nominal --simulate --output results/nominal.json
qguard schedule results/problem.json --method robust --simulate --output results/robust.json
qguard benchmark --quick --output results/quick
```

先比較三份 JSON 的 `starts` 與 `makespan_ticks`，再查看 `schedule.svg`。
`--simulate` 只評估名目情境；判斷穩健性要看 benchmark 的 held-out 結果。

## 第一層：理解我們在最佳化什麼

假設兩個 CX 分別作用在 (0,1) 與 (2,3)，硬體存在 (1,2) 的干擾連結。
兩個 CX 同時運作時，這條連結可能產生 ZZ 串擾；把其中一個延後就能減少重疊，
但其他 qubit 可能需要等待。

因此，多排一段時間有兩種相反影響：減少串擾、增加 idle decoherence。
電路 depth 也不等於實際時間；本專案用明確的 gate duration 計算 makespan。

先閱讀 `model.py` 的 `Circuit.dependencies()`。它記住每條 qubit 上的上一個閘，
建立必須遵守的先後關係。原始電路的閘編號順序不表示所有閘都必須循序執行。

## 第二層：讀懂排程搜尋

閱讀 `scheduling.py`：

1. `asap()` 用拓樸排序計算最早合法開始時間。
2. `conflicts()` 找出可能同時執行且存在干擾連結的雙量子位元閘。
3. `RiskModel` 對每個候選排程計算多情境風險。
4. `optimize()` 嘗試加入 a→b 或 b→a 的額外限制，保留一組較好的候選。
5. `exhaustive_oracle()` 在小型問題中枚舉所有限制選擇，用來檢查搜尋品質。

一定要能解釋：**這個搜尋是在「加入先後限制後重新做 ASAP」的集合中找答案。**
它沒有探索所有任意開始時間，所以小型窮舉也只保證這個集合內的最佳解。
Beam search 本身則是啟發式方法。

練習：修改 `--beam-width`、`--max-rounds`，觀察編譯時間與 training risk。
保留 makespan 上限，才能公平比較搜尋品質。

## 第三層：CVaR 解決哪一種問題

Mean risk 對所有校準情境取平均。CVaR 則關心損失最高的一段機率質量。
例如 alpha=0.8，就是最差的 20% 尾端。QGuard 用 lambda 混合平均與尾端風險。

五種基準中的 `scenario_mean` 很重要：它與 robust 使用相同訓練情境，差別只有
尾端權重。如此才能分辨收益來自「使用多個情境」，還是來自「額外重視高風險尾端」。

目前 16 個 training scenarios 與 24 個 test scenarios 是小型展示設定。
測試情境不能拿來挑選 lambda、sigma 或搜尋預算；若要調參，另外建立 validation set。

## 第四層：物理模型怎麼驗證

閱讀 `simulator.py` 的 `timeline()`，再讀 `idle_kraus()`。
時間區間採 [start,end)，因此前一個閘在 t 結束、下一個在 t 開始，不算重疊。
理想閘在結束時套用；區間內處理 idle channel 與 active gate 之間的 ZZ channel。

你應該能從解析式說明：

- 激發態人口以 exp(-t/T1) 衰減。
- 非對角 coherence 以 exp(-t/T2) 衰減。
- T2 必須不大於 2T1，才能得到非負的 pure dephasing rate。
- 每組 Kraus operators 滿足 Σ K†K = I，所以 channel 保持 trace。

這是離散的 gate-level 模型，沒有模擬驅動脈衝期間的完整 Hamiltonian。
Aer 與 NumPy 的一致性驗證的是數值傳播；它們共用事件產生器，因此事件邊界還要
靠解析測試檢查。也不能把兩個模擬器一致解讀成已經完成真機驗證。

練習：在四個 qubit 的兩個 CZ 上，設定開始時間為 0 與 5、duration 都為 8。
重疊是 3 ticks。查看 `test_half_open_overlap_exact_integrated_zz`，確定自己能手算相位。

## 第五層：如何讀實驗結果

`results.json` 保存所有原始 fidelity。`summary.csv` 的平均是跨電路平均；
`mean_per_circuit_p10` 是每個電路各自的第十百分位再平均，不是把所有樣本混在一起算。
GHZ 沒有可用的同時雙閘串擾，適合作為控制組；其不同 seed 在此產生相同電路，
不能當成獨立研究樣本。

如果 robust 的 training risk 降低，但某個測試電路的 fidelity 下降，首先檢查
idle 代價、狀態對 ZZ 的敏感度，以及 surrogate 忽略的相位抵消。
這些現象可以形成下一個研究問題：如何讓成本模型更貼近狀態層級誤差？

完整數據的解讀見 `results.md`。目前的小型合成實驗提供方法可行性證據，
尚不足以主張在所有電路或真實量子裝置上優於既有方法。

## 面對教授時，應該能回答的問題

| 問題 | 你需要掌握的重點 |
|---|---|
| 你新增了什麼？ | 明確的多情境風險模型、合法排程搜尋、事件式雜訊評估與可重現比較流程 |
| 串擾排程不是早就有人做？ | 是，需引用 XtalkSched；本案研究校準不確定性下的決策與尾端風險，尚未主張首次提出 |
| 為何不全部 serial？ | idle decoherence 與較長 makespan 可能抵銷減少串擾的收益 |
| 為何不直接最佳化 fidelity？ | 每個候選、每個情境都做密度矩陣模擬很昂貴；surrogate 的速度與準確度取捨需要評估 |
| 如何證明程式正確？ | 解析解、CPTP 檢查、合法性檢查、小型窮舉、Aer 交叉驗證，各自涵蓋不同層級 |
| 下一步怎麼做？ | 獨立校準資料、更多 topology、published baseline、validation set 調參、可擴展的模擬後端 |

## 建議的下一個開發里程碑

先固定本版作為 v0.1 實驗基線。下一版優先建立多個硬體拓樸、更多獨立電路，
再加入 validation/test 的完整分離及誤差區間。之後比較 mean、CVaR、worst-case
三種決策，才導入 CP-SAT 或 mapping 聯合最佳化。

你的 DD/QMDD 經驗最適合在評估成本成為瓶頸時接上：保留目前密度矩陣引擎作為
小型 reference，讓新後端先通過相同的物理測試。不要同時修改 scheduler 和
simulator，否則結果改變時會難以判斷原因。

初版程式由 AI 協作產生。實際展示或書審前，請逐層跑過上述練習、閱讀核心函式，
並清楚區分自己驗證與延伸的工作，以及已提供的基礎實作。

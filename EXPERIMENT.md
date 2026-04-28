# Phase 1 Experiment — Character Consistency Evaluation

[English](#english) | [中文](#中文)

---

<a name="english"></a>

## Experiment Overview

**Research question**: How much does the character card system improve AI-generated character consistency compared to generating without it?

**Method**: LLM-as-judge automated evaluation (0–10 integer score), using the same LLM provider and character card as the judge for all three groups.

**Design**: 3 groups × 5 scenes = 15 generations + 15 evaluations

| Group | Prompt content | Purpose |
|---|---|---|
| Control A | Scene description only — zero character information | Hard baseline |
| Control B | Scene + 2–3 sentence natural language character summary | Unstructured information baseline |
| Experimental | Scene + full structured character card system | System under test |

**Character used**: 富安悠 (Yu Tomiyasu), member of NEXZ (K-pop group). MBTI: ESFP. The character card was filled by the author based on observed behavior and personal interpretation — consistent with how fanfiction authors use this system in practice.

**Evaluation design note**: All three groups were evaluated using the same character card as the judge's reference standard, ensuring a consistent yardstick across groups.

---

## Character Card Summary (Experimental Group)

Key fields injected into the generation prompt:

- **Personality labels**: outwardly calm with an internal "switch"; completely comfortable with physical closeness; caretaking type; laughs unrestrainedly (nicknamed "witch laugh"); humble and sincere
- **Core values**: genuine treatment of others; gratitude to those who helped; care through action, not authority
- **Core fears**: using authority or status to suppress others; burdening teammates with his own emotional state
- **Behavioral patterns**:
  - When a teammate's mood is off: notices without being told; accompanies through action, not words; does not pressure the other to speak
  - When the "switch" activates (entertainment / game context): immediately becomes the atmosphere core, completely lets loose, unstoppable
  - When others need care: acts first — cooking, cleaning, accompanying — no words needed
- **Forbidden behaviors**: never treats anyone with cruelty; never uses his seniority to suppress teammates; absolutely will not eat carrots
- **Emotional patterns**: baseline is calm and inclusive; laughs loudly and uninhibitedly; tends to process negative emotions alone; when he can't hold on, seeks out Inoue Akira or Uemura Tomoya

**Control B natural language summary** (2–3 sentences injected into writing intent):
> 富安悠 is the oldest member of Japanese boy group NEXZ, MBTI ESFP. Usually calm and warm, but once his "switch" flips he becomes an unstoppable atmosphere anchor. Very caring toward younger members, expresses care through action rather than words. Will never treat people harshly or use his seniority to suppress teammates. When sad, tends to hold it in alone; only seeks someone out when he can't anymore.

---

## Scenes

| # | Scene | Core test point |
|---|---|---|
| 1 | Casual group conversation, relaxed atmosphere | ESFP switch mechanism, uninhibited laughter, baseline mood |
| 2 | A teammate is visibly low — Yu notices | Non-verbal emotional attunement; care through action; not forcing the other to speak |
| 3 | Being pranked by teammates, completely losing | Forbidden behavior: no cruel comeback, no pulling seniority — but also not soft |
| 4 | His own emotions hitting a limit; going to find Inoue Akira | Emotional recovery pattern: not speaking first, just getting close, waiting to be noticed |
| 5 | Being looked to for a decision as the oldest member | Humble and sincere; not pulling rank; not making unilateral calls |

---

## Results

| Scene | Control A | Control B | Experimental |
|---|---|---|---|
| Scene 1 — Casual conversation | excl.* | 10 | 10 |
| Scene 2 — Teammate is low | 9 | 10 | 10 |
| Scene 3 — Being pranked | 2 | 2 | 9 |
| Scene 4 — Emotional limit | 10 | 10 | 10 |
| Scene 5 — Decision moment | 10 | 10 | 10 |
| **Average** | **7.8** (4 scenes) | **8.4** | **9.8** |

\* Scene 1 Control A: the generation produced content centered on the blank-card character (Nishiyama Yuki) rather than Yu; the judge could not identify Yu and scored 0. Excluded as an edge case.

---

## Key Findings

### Finding 1 — Scene 3 shows the highest discriminative power (A=2, B=2, Exp=9)

The "switch mechanism" — fully letting loose in entertainment / game contexts — is the most discriminative trait in this experiment. Neither the zero-information baseline nor the natural language summary could transmit this fine-grained behavioral pattern. Both Control groups generated content where Yu appeared confused, helpless, and embarrassed — directly contradicting the card's behavioral rule that entering entertainment mode triggers an immediate personality shift to atmosphere anchor.

The character card's structured behavioral pattern format (trigger context + three-dimensional response) successfully conveyed this nuance, achieving a 9/10 score.

### Finding 2 — Scenes 2, 4, 5 show little differentiation across groups (all ≥ 9)

Traits like "caretaking type," "not forcing the other to speak," and "humble and sincere" are simply described and have clear boundaries. The natural language summary in Control B conveyed these adequately. This suggests the character card system's core value is in transmitting high-density, fine-grained behavioral rules — not replacing simple trait descriptions.

### Finding 3 — Control B average (8.4) vs Control A (7.8)

Natural language summaries do help for simple traits (Scenes 2, 4, 5) but provide no advantage for fine-grained behavioral patterns (Scene 3). The gap between Control B and Experimental is concentrated in the high-specificity scenes.

---

## Conclusions

> The structured character card system (Experimental, avg **9.8**) significantly outperforms both the natural language summary baseline (Control B, avg **8.4**) and the zero-information baseline (Control A, avg **7.8**) on character consistency.

> The system's core value lies in transmitting fine-grained, high-density behavioral rules that cannot be expressed in a 2–3 sentence summary. For simple, clearly-bounded traits, the advantage is smaller.

---

## Methodology Notes

- Judge model: same provider as generation (Gemini gemini-2.5-flash), BYOK
- All three groups used the same character card as the judge's reference (corrected from an initial design error where Control A/B used an empty card)
- Scene 3 Control A score of 0 is an edge case (judge could not identify the character), excluded from average
- Scene 5 Control A initial run produced Traditional Chinese output (language not specified) — re-run with language-explicit prompt; final score 10/10

---

---

<a name="中文"></a>

## 实验概述

**研究问题**：有了角色卡系统之后，AI 生成内容的角色一致性比没有角色卡时提升了多少？

**方法**：LLM-as-judge 自动化评估（0-10 整数分），三组评估时统一使用同一角色卡作为 judge 的参照标准。

**设计**：3 组 × 5 场景 = 15 次生成 + 15 次评估

| 组别 | Prompt 内容 | 目的 |
|---|---|---|
| Control A | 只有场景描述，零角色信息 | 硬基线 |
| Control B | 场景 + 2-3 句自然语言角色简介 | 非结构化信息基线 |
| Experimental | 场景 + 完整结构化角色卡系统 | 被测系统 |

**使用角色**：富安悠，日本男团 NEXZ 成员，MBTI: ESFP。角色卡由作者根据观察到的行为和个人理解填写——与同人文作者在实际使用中的方式一致。

---

## 角色卡核心内容（实验组）

注入 prompt 的关键字段：

- **性格标签**：外表沉稳内有开关；完全不排斥肢体接触；照顾型；大笑时极其放肆（被调侃巫婆笑声）；谦虚真诚
- **核心价值观**：真诚待人；对帮助过自己的人心存感激；用行动照顾身边的人而非施压
- **核心恐惧**：用权威或地位去压制别人；因自己的状态拖累队友
- **行为模式**：
  - 察觉到队友情绪不对：不需要对方说，用行动陪伴，不施压
  - 进入娱乐/游戏状态（开关打开）：立刻成为气氛核心，完全放开，谁也拦不住
  - 有人需要照顾时：主动行动，做饭、打扫、陪伴，用实际行动而非言语
- **人设红线**：绝对不会用刻薄方式对人；绝对不会以大哥身份压制队友；绝对不吃胡萝卜
- **情绪模式**：日常基调平和包容；大笑时完全放肆；负面情绪倾向自己先消化；扛不住时找井上阳或植村朋哉

**Control B 自然语言简介**（注入写作意图末尾）：
> 富安悠是日本男团NEXZ的最年长成员，MBTI是ESFP。平时沉稳温和，但一旦"开关打开"就会变成谁也拦不住的气氛担当。非常照顾年下成员，用行动而非言语表达关心。绝对不会用刻薄的方式对人，也不会以大哥身份压制队友。难过时倾向自己先扛，扛不住了才会找信任的人。

---

## 场景设计

| # | 场景 | 核心考验点 |
|---|---|---|
| 1 | 队内日常闲聊，气氛轻松 | ESFP 开关机制、放肆大笑、日常基调 |
| 2 | 队友状态明显低落，悠察觉到 | 非语言情绪感知；用行动照顾人；不强迫对方开口 |
| 3 | 被队友调侃整蛊，处于下风 | 人设红线：不刻薄反击，不用大哥身份压人，但也不是软弱 |
| 4 | 自己情绪扛不住，找井上阳 | 情绪恢复模式：不轻易开口，靠近但不一定直说，对方察觉 |
| 5 | 被期待当众表态 | 谦虚真诚；不倚老卖老；不以大哥身份拍板 |

---

## 实验结果

| 场景 | Control A | Control B | Experimental |
|---|---|---|---|
| 场景1 日常闲聊 | 排除* | 10 | 10 |
| 场景2 察觉队友低落 | 9 | 10 | 10 |
| 场景3 被队友整蛊 | 2 | 2 | 9 |
| 场景4 情绪扛不住 | 10 | 10 | 10 |
| 场景5 当众表态 | 10 | 10 | 10 |
| **平均分** | **7.8**（4场有效）| **8.4** | **9.8** |

\* 场景1 Control A：生成内容以空卡角色（西山裕贵）为主角，富安悠几乎未出现，judge 无法识别，给出 0 分。作为边界情况排除，不计入平均分。

---

## 核心发现

### 发现一：场景3 区分度最高（A=2, B=2, Exp=9）

「开关机制」——进入娱乐/游戏状态时完全放开、成为气氛核心——是本实验区分度最高的特质。两组对照均无法通过提示词传递这一细粒度行为模式，生成内容中富安悠表现出困惑、无助、狼狈等与设定严重冲突的行为（设定：进入娱乐状态时开关打开，立刻成为气氛核心，完全放开）。

角色卡通过结构化行为模式（触发情境 + 反应三维度）成功传递了这一特质，实现了 9/10 的评分。

### 发现二：场景 2/4/5 三组差异不显著（均 ≥ 9）

「照顾型」「不强迫对方开口」「谦虚真诚」等特质描述简洁、边界清晰，自然语言简介也能有效传递。说明角色卡系统的核心价值在于传递高密度、细粒度的行为规则，而非替代简单特质描述。

### 发现三：Control B（8.4）优于 Control A（7.8）

自然语言简介对简单特质有帮助（场景 2/4/5），但对细粒度行为模式无效（场景 3）。Control B 与 Experimental 的差距主要集中在高特异性场景。

---

## 实验结论

> 结构化角色卡系统（Experimental，平均 **9.8 分**）在角色一致性上显著优于自然语言简介基线（Control B，平均 **8.4 分**）和零信息基线（Control A，平均 **7.8 分**）。

> 系统的核心价值在于传递无法通过 2-3 句话表达的细粒度、高密度行为规则。对于描述简洁、边界清晰的简单特质，提升效果较小。

---

## 方法论备注

- Judge 模型：与生成相同的 provider（Gemini gemini-2.5-flash），BYOK 模式
- 三组评估时统一使用富安悠角色卡作为 judge 参照（修正了初版设计中 Control A/B 使用空卡评估的问题）
- 场景1 Control A 0 分为边界情况（judge 无法识别角色），排除计算
- 场景5 Control A 初次生成结果为繁体中文（未指定输出语言），重新生成后得分 10 分

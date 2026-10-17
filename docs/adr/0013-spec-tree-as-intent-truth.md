# 0013: 规格树是意图侧的真值，它的门禁跑在 CI 而不是本地 git hook

日期：2026-10-17
状态：已采纳

## 背景

本仓库的意图散落在三个地方：`README.md` 的模块地图、各模块 docstring 里
`"""One owner of …"""` 的段落、以及 `docs/adr/` 的决策记录。三者都写得不错，
但**没有任何机制在代码改动后指出其中哪一份过期了**。

这不是假设的问题。仓库里已经有一类反复出现的缺陷形态，docstring 自己记着：
同一条规则被复制到 Python、SQL CHECK、前端 JS 与测试夹具，副本随后各自漂移，
最终表现为对患者说了不该说的话——`core/evidence_strength.py` 的模块注释记录了
工作台告诉审核员 `low` 会被扣下、服务端却放行的那一次。

`docs/evidence-governance-standards.md` 与 `docs/adr/` 解决的是"决策为什么是这样"，
它们不解决"这段代码现在归哪条意图管、以及它是否还管得住"。缺的是**锚定**。

## 决策

引入 SpexCode 的 `.spec/` 规格树：`.spec/<project>/…/<node>/spec.md`，每个节点的
frontmatter `code:` 指向它治理的文件或具名单元（`path#Symbol` 形式为 anchor）。
漂移由 git 历史现算：自该 spec.md 最后一次提交以来，锚定单元的行区间是否被提交
改动过。命中即 `anchor-drift` 错误并指名越线的那次提交。

采用时确立了四条：

1. **以 `--pure` 引入。** 只新增 `.spec/` 与 `.gitignore` 的反向例外；不装 git hook、
   不改 `CLAUDE.md`/`AGENTS.md`、不引入 session 层、不生成 `.plugins/`。

2. **锚定的粒度只到真正承载契约的单元。** 例如 `core/store/review.py` 有 2515 行，
   锚的是 `ReviewStore._require_publishable` 而不是整个文件——文件级的 anchor 会让
   任何无关改动都触发漂移，噪音会训练人忽略这条检查。

3. **门禁跑在 CI，不接本机 `serverPolicy.chainedHooksPath`。** 阻断式 hook 技术上可接
   （实测：接上后服务器护栏仍优先，drift 门禁生效，逃生口 `--trailer "Spec-OK: <node>"` 可用），
   但它需要每台机器手工 `git config`，而 `spex init` 写入的内容不带机器可读标记，
   会被 `dev-policy-audit` 判定为"仓库覆盖 `core.hooksPath`"并尝试修复。
   一个会被别的工具悄悄撤销的护栏不是护栏。

4. **CI 必须 `fetch-depth: 0`。** 实测在真浅克隆（depth 1）下 lint 报
   `0 error 0 warning`，即便锚定单元已被改动。一个静默通过的检查比没有检查更糟。

## 被否掉的替代形态

**不引入规格树，靠现有的 `README.md` 模块地图 + docstring 维持。** 这是最强的替代，
也是现状。它的理由很硬：README 第 3 节的模块地图已经写得很细（依赖方向、已知回边、
每个模块的关键不变量），docstring 里的 `One owner of` 段落质量也高，
再引入一层就是第三个真值来源，而三个真值来源比两个更容易漂移。它输在一个可测的事实上：
**这些文档没有任何失效检测**。`core/evidence_strength.py` 的副本漂移能发生并活到
被模块注释记录下来的程度，正是因为当时没有任何东西在改动时指出"这条意图的锚被碰了"。
规格树增加的不是第三份散文，是**前两份散文的失效检测器**——spec 正文可以比 README 短，
它的价值在 `code:` 那一行。

**用代码注释代替 spec 树。** 理由：注释就在代码旁边，改代码的人一定看得见，不需要
另一棵树。它输在两处：注释无法被 lint 成"这条意图归哪个文件管"的关系；
且本仓库的注释纪律恰好走向反面——`docs/agents/` 要求注释只导航本地非显然决策，
契约级意图该进 spec 而不是注释。把契约写进注释会被本仓库自己的规范判为违规。

**接 `serverPolicy.chainedHooksPath`，让本机提交就被拦。** 理由见决策 3 的实测：
它确实能拦（`anchor-drift` 会中止提交），拦得比 CI 早，反馈更短。它输在**可持续性**：
配置不在仓库里、需要每台机器重复、且会被 `dev-policy-audit` 的修复路径撤销。
一个需要在每个 clone 上手工重建、并可能被自动修复静默拆掉的控制，
其可靠性低于一个在 CI 里对所有人一律生效的控制。代价是本机可以提交未消解的漂移，
由 CI 在 PR 上拦——反馈变晚，但不会失效。

**引入 session 层（`spex session new/review/merge`），用它的 worktree + 门控合并。**
理由：它自带隔离工作树与"worker 提议、人决定合并"的门控，与本仓库的 AFK 工作流气质接近。
它输在重叠：本仓库已有 `dev-start`/`dev-worktree` 与本机 `_runners/` 的 deliverable 模型，
再叠一层 worktree 编排会产生两套并存的分支生命周期。规格树（L0）与 session 层（L1）
本就分层，可以只要 L0。

## 后果

**更容易的。** "这段代码归哪条意图管"从搜索三份文档变成读一个 frontmatter。
锚定单元被改动而 spec 未动时，`spex spec lint` 指名越线的提交号，
`git diff $(git log -1 --format=%H -- <spec.md>)..HEAD -- <file>` 直接给出代码增量。
架构图与 spec 树同源：`spex diagram check` 会验证图中的每个框确实是该节点的子节点，
图不再是一份会单独过期的资产。

**更难的、以及被限制住的。** 规格树引入了新的维护义务：改动锚定单元时必须
同步改 spec，或用 `--trailer "Spec-OK: <node>"` 记录"契约仍成立"的真实理由。
这条义务的强度完全取决于 anchor 选得准不准——anchor 选得太粗（整个文件），
义务会退化成噪音；选得太细（类型/接口），重构会让它频繁报错而内容无意义，
所以本仓库的约定是锚行为承载单元（函数/方法/类），不锚纯类型声明。

**本记录明确不主张的。** 它不主张 `.spec/` 会取代 `README.md` 的模块地图或 `docs/adr/`。
三者的分工是：ADR 回答"为什么这样决定、否掉了什么"（人的判断，事后极难重建）；
README 回答"模块怎么划分、往哪读"（全局入口）；spec 节点回答"这段代码归属哪条意图、
它是否还成立"（可锚定、可检测）。规格树正文刻意比 README 短，因为长正文没有检测器保护，
只会变成第二个会过期的散文来源。

**一个已知的未决项。** `spec-tree` 作业目前**不是必需检查**（必需检查为
`Workflow policy` + `quality`）。也就是说今天它会在 PR 上变红，但不阻断合并。
把它提升为必需检查是一个独立决定，需要先观察它在本仓库的误报率。

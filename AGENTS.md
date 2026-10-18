本地开发启用环境：conda activate tracelet
随时维护.gitignore，不把非必要产物纳入git版本管理

## 架构决策记录

- 涉及对系统具有长期影响的架构选择时，使用项目级 [$adr](.agents/skills/adr/SKILL.md)，遵循 [ADR 规范](docs/adr/README.md)，先核查 [已知问题](docs/architecture/known-issues.md)及相关 ADR。
- 开发协作规范、文档管理、普通缺陷修复和局部重构不自动创建 ADR；是否触发以实际系统架构影响为准。
- AI 可调查和起草 Proposed；正式状态变更须依据用户对具体提案的明确决定，不能自行批准。事实须可验证，禁止编造性能数据或历史决策。

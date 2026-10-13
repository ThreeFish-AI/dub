# evals

- `evals.json`：输出质量评测。每条含 `prompt`、`expected_output`、`assertions`，用于人工或脚本化核对 Agent 是否按 SKILL.md 行事（授权铁律、服务收尾、朗读陷阱提醒、职责边界）。
- `trigger-evals.json`：触发评测，`{query, should_trigger}` 列表；正例 12 条、近邻负例 12 条（整集视频配音、转写、转码、剪辑、翻译、润色等相邻但不属于 dub 的任务）。

结构由 `tests/test_skill_spec.py` 校验（至少 3 个输出评测、正负例各至少 8 条）。

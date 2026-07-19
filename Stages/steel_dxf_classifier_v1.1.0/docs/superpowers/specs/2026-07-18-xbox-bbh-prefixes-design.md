# XBOX 与 BBH 内置类型扩展设计

## 目标

将 `XBOX`、`BBH` 加入 DXF 标题栏规格解析器的内置零件类型。二者是独立工程类型，不能分别归并为 `BOX`、`BH`，也不能继续以动态未登记前缀处理。

## 行为

- `XBOX300*300*10*10` 解析为 `part_type=XBOX`、`catalog_status=registered`；
- `BBH600*200*12*22` 解析为 `part_type=BBH`、`catalog_status=registered`；
- 批处理分别输出到 `<项目名称>_XBOX_dxf` 和 `<项目名称>_BBH_dxf`；
- 现有 `BOX`、`BH` 及其他类型行为保持不变；
- 标题栏证据、冲突处理和 fail-closed 边界不变。

## 实现范围

只扩展 `profile.py` 的登记表，并同步 README 与分类规则文档。解析器已经按完整字母前缀提取类型，因此不增加别名、字符串包含匹配或特殊分支。

## 验证

采用测试驱动：先增加 XBOX、BBH 解析失败用例，再更新登记表使其通过；另验证批处理生成两个独立目录，并用文档契约确保 README 和规则表同步。最后运行完整测试、编译、构建和项目整包 ZIP 校验。

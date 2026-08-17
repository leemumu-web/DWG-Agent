# BH 拆板前 DXF 真格式系统讲解

> 对象:`BH_拆板前_dxf汇总/`(352 张)
> 关键前提:**这些 DXF 不是 Tekla 直接导出,而是「Tekla 出图的 DWG」经 ODA(Open Design Alliance)转成 DXF**。因此格式 = **Tekla 的图形内容(图层/线型/块/几何/文字)+ ODA 的序列化外壳(头部/编码/块名/实体结构)**。最终拆板算法消费的正是这个"ODA 转化后"的 DXF。

---

## 0. 完整来源链

```
Tekla Structures(出图, 单件图)
  → 导出 DWG(内含 Tekla 图层命名、XKITLINE 线型、匿名块、GB2312 中文)
      → 人工上传
          → 服务器用 ODA 做 DWG→DXF 转换
              → 得到本目录的 .dxf
```

证据:
- `$DWGCODEPAGE = GB2312`(R2018)/ `ANSI_936`(R2000)——DWG 内部的中文代码页,是 DWG 残留而非 DXF 原生;
- `$LASTSAVEDBY = Creeken`——经人工/CAD 保存过;
- 两种 DXF 版本并存(见 §1),是 ODA 转换目标版本不同,而非 Tekla 导出差异。

## 1. 两种序列化世代(ODA 转换目标版本)

全语料只有两种 DXF 版本,内容同源、外壳不同:

| | R2018(`AC1032`) | R2000(`AC1015`) |
|---|---|---|
| 数量 | 285 张 | 67 张 |
| 代码页变量 | `$DWGCODEPAGE=GB2312` | `$DWGCODEPAGE=ANSI_936`(同是 GBK,拼写不同) |
| 中文文字 | **MIF 转义 `\M+5XXXX` 写在 ASCII 文本里** | **无转义,原始字节/已解码** |
| 直径符号 | `%%c` / `\M+5A6B5` | 直接字符 |
| 结构 | 基本一致(LINE/TEXT/INSERT/CIRCLE/ARC/HATCH/POINT) | 基本一致 |

**对拆板的意义**:文字解码必须同时覆盖两条路径。代码 `decode_cad_text_transport`(`dxf_io.py:39-57`)依次做 `decode_mif_to_unicode` → `ezdxf.decode_dxf_unicode` → `%%c`→Φ → cp936 直径符号方言,正是为了兼容两代。

## 2. 头部(ODA/AutoCAD 外壳)——对拆板真正有用的只有几个

全语料实测的头部变量:

| 变量 | 值 | 拆板是否使用 |
|---|---|---|
| `$INSUNITS` | **4(毫米),全部 352 张** | ✅ 单位唯一来源 |
| `$MEASUREMENT` | **1(公制),全部** | ✅(辅助) |
| `$DWGCODEPAGE` | GB2312 / ANSI_936 | ✅ 文字解码参考 |
| `$ACADVER` | AC1032 / AC1015 | ⚠️ 影响文字编码路径 |
| `$LTSCALE` | 1 / 10 / 20 / 25 / 30 / 40(与出图比例相关) | ❌ 算法不读 |
| `$EXTMAX` | 6040×4300 / 12080×8600 / …(随比例/图幅变) | ❌ 算法不读 |
| `$LUNITS/$LUPREC` | 2 / 4(十进制 4 位) | ❌ |
| `$DIMSCALE=1`、`$DIMASZ=0.18`、`$DIMTXT=0.18` | 尺寸样式 | ❌(尺寸是爆炸式,原生 DIMSTYLE 无用) |
| `$FINGERPRINTGUID`/`$VERSIONGUID` | AutoCAD 标准 GUID | ❌ |
| `$TDCREATE/$TDUPDATE` | 朱利安日期 | ❌ |

**关键结论**:算法实际只依赖 `$INSUNITS`(毫米)和文字编码;其余头部是 ODA/AutoCAD 的账本,不参与几何。

## 3. 图层表 = 语义角色(最核心的约定,来自 Tekla)

11 个图层,每个图层的**颜色是固定的、线型在图层级统一为 Continuous**(真正的线型在**实体级**区分,见 §5):

| 图层 | 固定颜色(ACI) | 实体类型构成(全语料) | 携带的语义 |
|---|---|---|---|
| `Part` | 2(黄) | **LINE 13966 + ARC 1769** | 板件轮廓(要拆的几何) |
| `Bolt` | 7(白) | **CIRCLE 9455 + LINE 52963 + INSERT 25** | 螺栓孔(圆 + 中心线) |
| `PartMark` | 3(绿) | TEXT 350 + LINE 700 | 件号 + 引线 |
| `BoltMark` | 3(绿) | TEXT 1854 + LINE 3720 | 螺栓直径 + 引线 |
| `Z-DIMENSIONS` | 1(红) | LINE 34214 + TEXT 6157 + POINT 11068 + ARC 623 + HATCH 623 | 尺寸(爆炸式) |
| `Z-DIMENSIONS-LINES` | 1(红) | LINE 883 | 尺寸线 |
| `Section` | 6(品红) | TEXT 64 + LINE 320 + HATCH 64 | 剖面符号 A-A |
| `DrawingSheet` | 5(蓝) | LINE 872 + TEXT 143 | 图框 |
| `OtherObjectType` | 1(红) | LINE 124 万 + TEXT 35484 + HATCH 15748 + ARC 13452 + CIRCLE 96 | 标题栏/材料表 |
| `Defpoints` | 7(白) | (辅助点) | 尺寸定位点,忽略 |
| `0` | 7(白) | **INSERT 15405** | 块引用宿主层 |

> 这是 Tekla 的默认图层命名/配色规范(参见 Tekla 文档 [Layers in exported DWG/DXF drawings](https://support.tekla.com/zh-hans/doc/tekla-structures/2021/int_layers_in_drawings_exported_to_dwg_dxf_files)、[Export to DWG/DXF](https://support.tekla.com/zh-hans/doc/tekla-structures/2025/int_export_to_dwg_or_dxf))。算法 `bh_dialect.py:109-138` 的 `RoleRule` 表就是把这套图层名映射成语义角色。

## 4. 线型表 = 可见性(来自 Tekla 的自定义线型)

| 线型名 | 描述(实测 pattern) | 语义 | 在哪个图层实体上 |
|---|---|---|---|
| `XKITLINE00` | 实线 | **可见边** | Part(9559)、Bolt 中心线(34200) |
| `XKITLINE01` | 短划线 `_ _ _` | 隐藏(短) | 少量 |
| `XKITLINE02` | 点划线 `__ __` | **中心线** | Bolt 俯视图三笔符号(27330) |
| `XKITLINE03` | `___ _ ___` | 中心线变体 | 少量 |
| `XKITLINE04` | 点线 `...` | **隐藏边** | Part(5393) |
| `XKITLINE05` / `XKITLINE06` | 混合 | 边界变体 | 少量 |
| `Continuous` | 实线 | 可见(部分图纸用这个替代 XKITLINE00) | Part 517 / Bolt 600 |
| `DOT2` | 点线 | 隐藏(替代 XKITLINE04) | Part 266 |
| `DASHEDX2` | 长划线 | 边界(替代) | Bolt 288 |

**关键结论**:
1. **可见/隐藏不是靠图层,是靠实体级线型**——`Part` 层上同时有 `XKITLINE00`(实)和 `XKITLINE04`/`DOT2`(隐)。算法必须先按线型分可见性(`bh_dialect.py:51-55, 75-78`)。
2. **同一语义有拼写变体**(XKITLINE00 vs Continuous、XKITLINE04 vs DOT2)——`bh_dialect.py:40-43` 注释专门说明这是"跨 DWG/DXF 世代拼写差异",代码把 `XKITLINE04`+`DOT2` 都归一为 HIDDEN。

## 5. 块结构 = 视图分组(ODA 序列化的匿名块)

- 模型空间:**15409 个 INSERT,0 个直接实体**;所有 INSERT 都在 `0` 图层。
- 块定义:全匿名(`*A1…*An`、`*Model_Space`、`*Paper_Space*`),**0 个命名块**。
- 每张图 29~54 个匿名块,每个块 ≈ Tekla 的一个语义单元:

| 块 | 内容 | 语义 |
|---|---|---|
| `*A2` | Part 11 LINE(实+隐) bbox 2383×1500 | 主视图(腹板立面) |
| `*A4` | Part 6 LINE bbox 2383×500 | 俯视图(翼缘平面) |
| `*A1`/`*A3` | Bolt CIRCLE+LINE | 两视图的螺栓孔 |
| `*A8…*A20` | Z-DIMENSIONS 文字 | 各尺寸值 |
| `*A21` | BoltMark 文字 | 螺栓直径 |
| `*A22` | PartMark 文字 | 件号 |
| `*A23` | Section 文字 | 剖面符号 |
| `*A5` | DrawingSheet | 图框 |
| `*A6`/`*A7` | OtherObjectType(标题栏) | 材料表/图号 |

算法 `decode_source_document`(`bh_source.py:513-580`)把每个顶层 INSERT 当 `SourceContainer`,递归 `virtual_entities()` 展开,并把块名/实例下标/变换链记成 provenance。

## 6. 实体级事实(拆板算法真正要"吃"的)

### 6.1 尺寸全部是"爆炸式",没有原生 DIMENSION

全语料 **0 个原生 `DIMENSION` 实体**。尺寸 = `Z-DIMENSIONS` 层上的 **分离 LINE + TEXT + POINT**(+少量 ARC/HATCH 做箭头/装饰)。这就是代码要 `_add_exploded_dimensions`(`bh_associations.py:546+`)从"文字+线"重建尺寸的原因。

### 6.2 螺栓孔的两种视图表达

- 主视图:`Bolt` 层 `CIRCLE`(孔径=直径,r=13→φ26)+ `XKITLINE00` 十字中心线;
- 俯视图:`Bolt` 层 `XKITLINE02` **三笔符号**(1 长竖线 + 2 短横),无圆。代码 `_edge_view_symbol_centers`(`bh_extractor.py:102-154`)按三笔对称性反推孔中心。

### 6.3 变截面/隐藏线/圆弧真实存在

- `Part` 层有 **1769 个 ARC**——圆弧端板/圆角真实存在,必须 `arc_points` 折线 + `ring_to_bulge_contour` 贴回 bulge;
- `Part` 层 5393 条 `XKITLINE04` + 266 条 `DOT2` 隐藏线——变截面构件的翼缘/加劲板投影,必须排除出实体边界但用于"桥接"缺口;
- 标题栏 `OtherObjectType` 有 124 万条 LINE + 1.5 万 HATCH + 1.3 万 ARC——纯装饰,靠图层直接丢弃。

### 6.4 XDATA 只是 AutoCAD 家事

APPID 只有 `ACAD` 和 `ACAD_MLEADERVER`(AutoCAD 标准应用),无 Tekla 业务元数据。拆板不读 XDATA。

## 7. 文字编码(两条世代路径)

- 代码页:`GB2312`(R2018)/ `ANSI_936`(R2000),实质都是 GBK 中文;
- R2018 文字用 **MIF 转义** `\M+5XXXX`(每 4 位十六进制一个 code unit)携带中文/Φ;
- 直径符号三种拼写:`%%c`、`%%C`、cp936 字节 `A6B5`(可能被解成 `¦µ`)→ 都归一为 Φ(`dxf_io.py:53-57`);
- 解码只做"传输层还原",不含工程语义;语义在 `bh_semantics.py` 的正则里做。

## 8. 单位与比例

- 坐标单位 = `$INSUNITS=4`(毫米),**坐标已经是 1:1 毫米**;
- 标题栏 `1:10/1:20/…` 只是出图显示比例,不参与换算;
- `$LTSCALE`(1/10/20/25/30/40)与出图比例相关,`$EXTMAX`(6040×4300 等)是"几何+放大的图框"的模型空间范围,二者都是外壳账本,拆板不读。

## 9. 一句话总结

```
这些 DXF = ODA 把「Tekla 单件图 DWG」转成的 DXF:
  · 头部/编码/块名/实体结构 = ODA/AutoCAD 外壳(只关心 $INSUNITS + 文字编码)
  · 图层命名 + 固定颜色 = Tekla 语义约定(Part/Bolt/PartMark/BoltMark/Z-DIMENSIONS/…)
  · XKITLINE 线型 = Tekla 可见性约定(实线/隐藏/中心线,且有拼写变体)
  · 匿名块 INSERT = 视图/标注/表格的分组
  · 尺寸 = 爆炸式 LINE+TEXT+POINT(无原生 DIMENSION)
  · 文字 = GB2312/GBK,两代编码(MIF 转义 vs 原始)
```

**拆板算法必须兼容两条序列化世代、三种隐藏线拼写、爆炸式尺寸、变截面隐藏线、圆弧端、螺栓孔双表达——这正是上一轮"根本不足"中"整套解析被死死绑定在 Tekla 单一导出画像上"的格式侧体现。**

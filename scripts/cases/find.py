#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""案例库检索：中文自然语言 + 封闭标签词表 + 同义概念扩展。

三层召回，逐层兜底，任何非空输入都不返回空列表：
  1) 字面层：把查询切片（原词 / 2 字滑窗 / 3 字滑窗 / 去虚词子片段）在
     tags、name、type、problems、transferable、location、architect 上
     按字段权重加权命中（标签最高，标题/类型次之，正文最低）。
  2) 概念层：CONCEPTS 同义/近义表把大白话映射到词表里真实存在的标签，
     例如「滨水」→ 与湿地景观连续 / 可淹没游憩带 / 渠化河道自然化。
  3) 兜底层：以上都未命中时，用单字重合度给出最接近的结果并标注不确定。

用法示例：
  python find.py --problem 滨水修复 --limit 3
  python find.py --problem 旧厂房改造 --type 文化
  python find.py --tags 雨水滞蓄 --limit 3
  python find.py --problem 山地建筑 --json
  python find.py --list-tags
  python find.py --list-synonyms

退出码：0 表示检索完成（含兜底结果与空输入提示）；2 表示案例库缺失或无法解析。
"""

import argparse
import difflib
import json
import os
import re
import sys

SEPARATORS = re.compile(r"[,，;；、/\s]+")
CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")
REL_EXACT = "exact"
REL_TERM_IN_VALUE = "term⊂value"
REL_VALUE_IN_TERM = "value⊂term"
REL_FUZZY = "近似词表"
REL_CONCEPT = "同义概念"
REL_FALLBACK = "字面兜底"
FUZZY_CUTOFF = 0.62

# 字段权重：标签最高，名称/类型次之，正文最低，元数据仅作弱信号。
FIELD_WEIGHTS = {
    "tags": 10.0,
    "name": 6.0,
    "type": 6.0,
    "problems": 3.5,
    "transferable": 3.0,
    "architect": 2.0,
    "location": 1.5,
    "country": 0.8,
}

# 虚词：仅用于额外切出更干净的查询片段，不删除原词。
STOPWORD_CHARS = "的了着过是在和与及或等吗呢吧啊呀嘛么哦这那个些我你他她们您请帮想要去来把被让给对从到就都也还又很更最某何如怎什样为以于其之而但所以因此很"
STOPWORD_SPLIT = re.compile("[%s]+" % STOPWORD_CHARS)

# 概念标签贡献系数（按概念内标签的重要度递减）与每概念每案例的标签上限。
CONCEPT_FACTORS = (0.55, 0.45, 0.38)
CONCEPT_TAG_CAP = 2
# 同一案例命中多个标签时按排名递减求和，避免一个泛指词点亮一串标签。
TAG_DECAY = (1.0, 0.65, 0.45, 0.35, 0.3, 0.25, 0.2)
TAG_DECAY_FLOOR = 0.2
# 整个查询词（而非零散切片）命中标签/概念时，额外奖励精确性。
PHRASE_BONUS = 6.0
COVERAGE_BONUS = 1.5
TOKEN_BONUS = 0.5
FUZZY_FACTOR = 0.30
FALLBACK_LIMIT = 3

# ---------------------------------------------------------------------------
# 同义/近义概念表：核心资产。
# 每组 = (概念名, 用户可能说的说法, 词表里真实存在的标签[按重要度排序])。
# 标签字符串必须逐字匹配 assets/case-library.json 的 tag_vocabulary；
# --list-synonyms 会自检并列出越界标签。
# ---------------------------------------------------------------------------
CONCEPTS = (
    ("滨水岸线",
     ("滨水", "岸线", "水岸", "滨河", "河岸", "水滨", "临水", "河滨", "滨江", "江岸", "湖滨", "滨海", "水边"),
     ("与湿地景观连续", "可淹没游憩带", "渠化河道自然化", "弹性设计", "水院组织轴线")),
    ("湿地",
     ("湿地", "沼泽", "滩涂", "湿地公园", "人工湿地", "浅滩"),
     ("湿地净化梯田", "与湿地景观连续", "乡土与自演替", "生产性景观")),
    ("雨水滞蓄",
     ("雨水", "雨洪", "海绵城市", "海绵", "滞蓄", "蓄水", "下凹绿地", "雨水花园", "雨水管理", "渗透", "排涝", "暴雨"),
     ("雨水滞蓄", "雨水滞蓄湖", "雨水滞蓄地形", "蓝绿基础设施", "弹性设计")),
    ("防洪",
     ("防洪", "防汛", "洪水", "水位", "涨落", "淹没", "耐淹", "洪涝"),
     ("可淹没游憩带", "防洪与游憩叠合", "弹性设计", "渠化河道自然化")),
    ("河道治理",
     ("河道", "渠化", "硬质河道", "河床", "水体净化", "水质", "清淤"),
     ("渠化河道自然化", "湿地净化梯田", "蓝绿基础设施")),
    ("生态修复",
     ("修复", "生态修复", "治理", "净化", "生态", "复育", "生态性"),
     ("湿地净化梯田", "渠化河道自然化", "蓝绿基础设施", "乡土与自演替")),
    ("工业遗产",
     ("工业遗产", "工业遗存", "厂房改造", "旧厂房", "工业建筑", "工厂", "车间", "仓库", "旧仓库", "产业建筑", "烟囱", "铁轨"),
     ("工业遗存保留", "旧材回用", "地标构件保留（烟囱）", "大跨无柱展厅", "新旧并置")),
    ("旧改更新",
     ("更新", "改造", "旧改", "存量", "更新改造", "老城", "旧城", "再利用", "活化", "翻新"),
     ("微更新", "有机更新", "新旧并置", "旧材回用", "分期生长")),
    ("微更新",
     ("微更新", "渐进", "小规模", "针灸", "点状更新", "绣花"),
     ("微更新", "分期生长", "低成本运维", "临时设施转永久")),
    ("历史街区",
     ("历史街区", "老城区", "古城", "城中村", "历史风貌", "保护", "文脉", "肌理保护", "老房子"),
     ("城中村与历史并置", "新旧并置", "微更新", "有机更新")),
    ("校园",
     ("校园", "学校", "教育建筑", "校区", "大学", "小学", "中学", "幼儿园", "院校", "教学楼", "操场", "学院"),
     ("无墙教室", "聚落化布局", "屋顶即活动场", "合院群落", "环形屋顶")),
    ("山地坡地",
     ("山地", "坡地", "台地", "丘陵", "高差", "起伏", "坡向", "山体", "梯田", "错层", "跌落"),
     ("地形嵌入", "剖面式地形折叠", "人工地形堆山", "地形化曲面", "大尺度草坡", "湿地净化梯田")),
    ("地形塑造",
     ("地形", "地景", "起伏地形", "堆山", "土方", "微地形", "覆土", "下沉", "台层"),
     ("人工地形堆山", "地形化曲面", "剖面式地形折叠", "地形嵌入", "覆土消隐", "下沉庭院与地景")),
    ("住宅",
     ("住宅", "居住", "住区", "小区", "公寓", "楼盘", "集合住宅", "住宅区", "住房", "宿舍", "村落住宅"),
     ("邻里单位", "朝南行列式", "空中连廊", "空中连桥", "院落尺度延续", "楼房化的合院", "分级绿地")),
    ("高密度",
     ("高密度", "高容积率", "紧凑", "密集", "集约", "超高密度", "高层", "塔楼"),
     ("空中连廊", "空中连桥", "混合功能立体街区", "公共空中花园", "城市尺度地标", "模数化单元")),
    ("社区营造",
     ("社区", "邻里", "社群", "居民参与", "共建", "街坊"),
     ("邻里单位", "参与式规划", "分级公共空间", "分级绿地", "步行半径")),
    ("儿童",
     ("儿童", "幼儿", "游乐", "玩耍", "亲子", "游戏场地", "儿童活动", "游戏", "嬉戏", "游乐场"),
     ("屋顶即活动场", "环形屋顶", "无墙教室", "大草坪作为聚集器", "公共空中花园")),
    ("适老",
     ("老年", "养老", "适老", "长者", "老人", "无障碍出行"),
     ("分级公共空间", "步行半径", "分级绿地", "邻里单位")),
    ("无障碍",
     ("无障碍", "坡道", "可达性", "轮椅", "残障", "残疾人", "通行", "易达", "平层入户"),
     ("步行优先", "步行半径", "架空底层·连续地面", "多首层", "路面归还步行", "无车社区")),
    ("入口首层",
     ("入口", "出入口", "门厅", "首层", "地面层", "街面", "入户", "落客"),
     ("多首层", "三向开口", "架空底层·连续地面", "抬升体量形成连续地面", "贴线与退让")),
    ("屋顶",
     ("屋顶", "屋面", "天台", "屋上", "顶面", "屋面平台"),
     ("可上人屋面", "可上人屋面平台", "环形屋顶", "屋顶农田", "屋顶即活动场", "树穿屋面", "公共空中花园")),
    ("屋顶花园",
     ("屋顶花园", "屋顶绿化", "空中花园", "屋顶农场", "屋顶菜园", "屋顶农田", "种植屋面", "空中庭院"),
     ("可上人屋面", "屋顶农田", "公共空中花园", "可上人屋面平台", "屋顶即活动场", "环形屋顶")),
    ("日照采光",
     ("日照", "采光", "朝向", "朝南", "南向", "太阳", "遮阳", "遮挡", "日照间距", "间距", "北向", "光环境"),
     ("朝南行列式", "天窗遮光", "贴线与退让", "邻里单位")),
    ("通风",
     ("通风", "穿堂风", "风环境", "气流", "自然通风", "导风"),
     ("三向开口", "架空底层·连续地面", "大跨无柱展厅", "朝南行列式")),
    ("光影",
     ("光影", "采光口", "天窗", "遮光", "光线", "自然光", "光斑", "暗厅"),
     ("天窗遮光", "几何取景口", "剖面取景口", "视线的强制框定")),
    ("视线取景",
     ("视线", "取景", "框景", "对景", "景观面", "视野", "框景窗", "观景"),
     ("剖面取景口", "几何取景口", "视线的强制框定", "多首层")),
    ("公共空间",
     ("公共空间", "开放空间", "城市空间", "广场", "聚集", "公共性", "公共场所", "市民", "街道生活"),
     ("分级公共空间", "大草坪作为聚集器", "交叉口转为广场", "无中心", "环形公共层")),
    ("步行慢行",
     ("步行", "慢行", "步道", "人行", "行人", "步行系统", "行走", "漫步", "散步"),
     ("步行优先", "步行半径", "路面归还步行", "环线与多路径", "架空的多路径桥", "无车社区")),
    ("交通车行",
     ("交通", "车行", "机动车", "停车", "车流", "道路", "街道", "马路", "过境", "车行道"),
     ("九宫格限过境交通", "路面归还步行", "街道断面重做", "支路下沉穿越", "无车社区", "交叉口转为广场")),
    ("连接缝合",
     ("连接", "连通", "联系", "串联", "缝合", "接驳", "连桥", "连廊", "天桥"),
     ("空中连廊", "空中连桥", "架空底层·连续地面", "多首层", "环线与多路径")),
    ("立体分层",
     ("立体", "竖向", "多层地面", "标高", "架空", "立体化", "分层", "垂直"),
     ("多首层", "架空底层·连续地面", "混合功能立体街区", "空中连廊", "环形公共层")),
    ("植物植被",
     ("植物", "植被", "绿化", "草木", "乔木", "树林", "种植", "栽植", "林荫", "苗圃"),
     ("乔木骨架", "条带式植被交替", "三层结构", "序列花园", "分级绿地", "乡土与自演替", "绿地保留")),
    ("乡土低维护",
     ("乡土", "本地植物", "低维护", "低养护", "自演替", "野化", "自然演替", "野草", "野生"),
     ("乡土与自演替", "最小干预", "低成本运维", "几何与野性并置", "生产性景观")),
    ("园林景观",
     ("景观", "园林", "造园", "花园", "绿地", "公园", "园子", "绿化带"),
     ("序列花园", "大草坪作为聚集器", "分级绿地", "边界种植带", "与网格对比的自然式", "白色园与黑色园对比")),
    ("城市家具",
     ("家具", "城市家具", "座椅", "设施", "装置", "小品", "标识", "垃圾桶", "路灯"),
     ("城市家具整合", "单一构件整合设施", "全球物件收藏", "红色亭子（folie）重复")),
    ("基础设施",
     ("基础设施", "高架", "铁路", "废弃铁路", "高线", "立交", "桥", "轨道", "工业设施"),
     ("基础设施再利用", "保留铁轨细节", "空中连廊", "步行优先", "架空的多路径桥")),
    ("坡屋顶",
     ("坡屋顶", "坡屋面", "双坡", "屋顶形态", "传统屋面", "大屋顶"),
     ("坡屋顶改造", "大屋顶下的自由平面", "合院群落", "聚落化布局")),
    ("草坪场地",
     ("草坪", "草地", "草坡", "开敞", "大草坪", "聚集场地", "空地"),
     ("大草坪作为聚集器", "大尺度草坡", "边界种植带", "屋顶即活动场")),
    ("生产性景观",
     ("生产性", "农业", "农田", "耕作", "菜园", "稻田", "种植田", "农事"),
     ("生产性景观", "屋顶农田", "乡土与自演替")),
    ("材料质感",
     ("材料", "材质", "质感", "砖", "混凝土", "清水混凝土", "瓦", "石材", "面层", "饰面", "涂料"),
     ("材料本色", "单一材料", "旧材回用", "砌筑即装饰", "以铺装统一")),
    ("旧材回用",
     ("旧材", "旧砖", "回收", "再用", "废料", "循环利用", "拆料", "老砖"),
     ("旧材回用", "工业遗存保留", "保留铁轨细节", "砌筑即装饰")),
    ("构造节点",
     ("构造", "节点", "细部", "工艺", "砌筑", "建造", "施工", "做法"),
     ("砌筑即装饰", "材料本色", "单一构件整合设施", "模数化单元")),
    ("结构单元",
     ("结构", "单元", "模块", "模数", "预制", "装配", "重复", "标准化", "拼装"),
     ("结构单元重复", "模数化单元", "单元重复", "红色亭子（folie）重复", "单一构件整合设施")),
    ("大跨自由平面",
     ("大跨", "无柱", "自由平面", "展厅", "柱网", "开敞平面", "大空间", "展场"),
     ("大跨无柱展厅", "大屋顶下的自由平面", "结构单元重复", "倾斜体量")),
    ("院落合院",
     ("院落", "庭院", "合院", "天井", "院子", "围合", "四合院", "院墙"),
     ("院落序列", "合院群落", "院落尺度延续", "楼房化的合院", "下沉庭院与地景", "水院组织轴线")),
    ("水院",
     ("水院", "水池", "水面", "水景", "浅水", "镜面水"),
     ("水院组织轴线", "与湿地景观连续", "下沉庭院与地景", "雨水滞蓄")),
    ("界面边界",
     ("界面", "边界", "围墙", "临街", "贴线", "退线", "边界处理", "沿街"),
     ("贴线与退让", "边界种植带", "三向开口", "体量切分", "城市断裂带")),
    ("体量塑造",
     ("体量", "形体", "造型", "切分", "组合", "倾斜", "悬浮", "折板", "标志体量"),
     ("体量切分", "倾斜体量", "三向开口", "几何转译", "城市尺度地标")),
    ("新旧并置",
     ("新旧", "并置", "对比", "加建", "植入", "新旧关系", "叠加"),
     ("新旧并置", "旧材回用", "工业遗存保留", "城市断裂带")),
    ("地标",
     ("地标", "标志性", "形象", "名片", "城市名片", "识别性", "打卡"),
     ("城市尺度地标", "城市断裂带", "地标构件保留（烟囱）", "视线的强制框定")),
    ("低成本运维",
     ("低成本", "造价", "经济", "运维", "投资", "可持续运营", "省钱", "预算"),
     ("低成本运维", "最小干预", "临时设施转永久", "材料本色")),
    ("分期生长",
     ("分期", "生长", "弹性", "可变", "预留", "渐进式", "二期", "留白"),
     ("分期生长", "弹性设计", "临时设施转永久", "无程序预设")),
    ("参与式",
     ("参与", "共建", "公众参与", "工作坊", "居民共创", "自建", "共创"),
     ("参与式规划", "无程序预设", "临时设施转永久", "分级公共空间")),
    ("生态低碳",
     ("生态", "低碳", "节能", "绿色", "可持续", "环保", "碳排放", "能耗"),
     ("低能耗建造", "蓝绿基础设施", "乡土与自演替", "最小干预", "生产性景观")),
    ("四季时间",
     ("季节", "四季", "时令", "变化", "时序", "春夏", "秋冬"),
     ("季节变化", "动态花园（Garden in Motion）", "分期生长", "分段主题")),
    ("城市网格",
     ("网格", "格网", "街区", "方格", "肌理", "城市肌理", "九宫格"),
     ("与网格对比的自然式", "非层级网格", "九宫格限过境交通", "点线面系统", "三分区地面语言")),
    ("路径动线",
     ("路径", "动线", "游线", "环路", "路径系统", "多路径", "路线", "漫步道"),
     ("环线与多路径", "点线面系统", "架空的多路径桥", "步行优先", "分段主题")),
    ("铺装地面",
     ("铺装", "地面", "地面材料", "硬质", "广场铺地", "地表", "地面语言"),
     ("以铺装统一", "三分区地面语言", "材料本色", "可上人屋面")),
    ("城中村",
     ("城中村", "自建房", "握手楼", "村改", "村落", "旧村", "宅基地"),
     ("城中村与历史并置", "微更新", "街道断面重做", "分期生长")),
    ("文化建筑",
     ("文化建筑", "博物馆", "美术馆", "展览", "展厅", "剧院", "剧场", "艺术中心", "文化中心", "纪念馆"),
     ("大跨无柱展厅", "几何转译", "旧材回用", "城市尺度地标", "几何取景口")),
    ("混合功能",
     ("混合功能", "综合体", "功能混合", "商住", "业态", "街区复合", "上住下商"),
     ("混合功能立体街区", "环形公共层", "分级公共空间", "多首层")),
    ("无车社区",
     ("无车", "禁车", "共享街道", "人车分流", "限行"),
     ("无车社区", "路面归还步行", "步行优先", "九宫格限过境交通")),
    ("微气候",
     ("微气候", "热岛", "遮阴", "荫凉", "通风廊道", "夏季", "闷热"),
     ("乔木骨架", "天窗遮光", "三层结构", "大草坪作为聚集器")),
    ("视觉框定",
     ("框定", "取景口", "洞口", "开窗", "对位", "轴线对位"),
     ("视线的强制框定", "几何取景口", "剖面取景口", "三向开口")),
    ("城市断裂",
     ("断裂", "割裂", "被切断", "缝合城市", "跨越", "地块割裂"),
     ("城市断裂带", "空中连廊", "空中连桥", "支路下沉穿越")),
    ("动线过境",
     ("过境", "穿越", "车流穿越", "快速路", "干道", "下沉车道"),
     ("九宫格限过境交通", "支路下沉穿越", "街道断面重做", "路面归还步行")),
)


def script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def library_path():
    return os.path.normpath(os.path.join(script_dir(), "..", "..", "assets", "case-library.json"))


def load_library(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "cases" not in data or "tag_vocabulary" not in data:
        raise ValueError("case-library.json 缺少 cases 或 tag_vocabulary")
    return data


def norm(text):
    return re.sub(r"\s+", "", str(text).strip().lower())


def has_content(text):
    """是否含中文或字母数字（纯标点/空白视为无内容）。"""
    for ch in str(text):
        if CJK_CHAR.match(ch) or ch.isalnum():
            return True
    return False


def split_terms(values):
    out = []
    for value in values or []:
        for part in SEPARATORS.split(value):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def flatten_vocabulary(vocab):
    """返回 [(category, tag), ...]"""
    pairs = []
    for category, tags in vocab.items():
        for tag in tags:
            pairs.append((category, tag))
    return pairs


def length_factor(size):
    """切片越长越具体，权重越高；2 字为标准单位。"""
    if size <= 1:
        return 0.35
    return {2: 1.0, 3: 1.3, 4: 1.5}.get(size, min(1.5 + 0.15 * (size - 4), 2.4))


def tokenize(segment):
    """把一个查询切片拆成 token: 原词 + 2/3 字滑窗 + 去虚词子片段。"""
    seg = norm(segment)
    tokens = {}

    def add(tok, base):
        if len(tok) < 2 and len(seg) > 1:
            return
        if not has_content(tok):
            return
        if tok not in tokens or tokens[tok] < base:
            tokens[tok] = base

    if not seg:
        return tokens
    add(seg, length_factor(len(seg)))
    for size in (2, 3):
        if len(seg) > size:
            for i in range(len(seg) - size + 1):
                add(seg[i:i + size], length_factor(size) * 0.9)
    for part in STOPWORD_SPLIT.split(seg):
        if 2 <= len(part) < len(seg):
            add(part, length_factor(len(part)) * 0.95)
    return tokens


def build_tokens(terms):
    """合并全部查询词的 token；同一 token 保留最高权重并记下来源词。"""
    tokens = {}
    for term in terms:
        for tok, base in tokenize(term).items():
            if tok not in tokens or tokens[tok]["base"] < base:
                tokens[tok] = {"base": base, "term": term}
    return tokens


def field_values(case):
    """案例的可检索字段；problems/transferable 承担正文角色。"""
    return {
        "tags": list(case.get("tags") or []),
        "name": [case.get("name") or ""],
        "type": list(case.get("type") or []),
        "problems": list(case.get("problems") or []),
        "transferable": list(case.get("transferable") or []),
        "architect": [case.get("architect") or ""],
        "location": [case.get("location") or ""],
        "country": [case.get("country") or ""],
    }


def case_blob(case):
    parts = []
    for values in field_values(case).values():
        parts.extend(values)
    return norm(" ".join(parts))


def build_index():
    surface_concepts = {}
    concept_tags = {}
    for name, terms, tags in CONCEPTS:
        concept_tags[name] = tags
        for term in terms:
            surface_concepts.setdefault(norm(term), []).append(name)
    return {"surface_concepts": surface_concepts, "concept_tags": concept_tags}


def lookup_concepts(token, index):
    """token 与概念说法互含即视为同一概念（两侧都 ≥2 字，避开单字噪声）。"""
    tok = norm(token)
    best = {}
    for surface, names in index["surface_concepts"].items():
        if tok == surface:
            factor = 1.0
        elif len(tok) >= 2 and len(surface) >= 2 and (tok in surface or surface in tok):
            factor = 0.85
        else:
            continue
        for name in names:
            if name not in best or best[name][1] < factor:
                best[name] = (surface, factor)
    return [(name, surface, factor) for name, (surface, factor) in best.items()]


def expand_to_vocabulary(term, vocab_pairs, limit=2):
    """子串未命中时，用近似度扩到词表里的标签。"""
    tags = [tag for _, tag in vocab_pairs]
    lookup = {norm(tag): tag for tag in tags}
    close = difflib.get_close_matches(norm(term), list(lookup.keys()), n=limit, cutoff=FUZZY_CUTOFF)
    hits = []
    for key in close:
        tag = lookup[key]
        ratio = round(difflib.SequenceMatcher(None, norm(term), key).ratio(), 2)
        hits.append((tag, ratio))
    return hits


def resolve_tokens(tokens, cases, index, vocab_pairs):
    """全局判定每个 token 的来源：直接命中 / 概念扩到 / 需要近似兜底。"""
    blobs = [case_blob(case) for case in cases]
    resolution = {"concepts": {}, "fuzzy": {}, "direct": {}}
    for tok in tokens:
        concepts = lookup_concepts(tok, index)
        resolution["concepts"][tok] = concepts
        direct = any(tok in blob for blob in blobs)
        resolution["direct"][tok] = direct or bool(concepts)
        resolution["fuzzy"][tok] = [] if resolution["direct"][tok] else expand_to_vocabulary(tok, vocab_pairs, 3)
    return resolution


def resolve_tag_terms(term, vocab_pairs, index):
    """把标签查询词解析成词表真实标签：精确 > 互含 > 概念 > 近似。"""
    tag_list = [tag for _, tag in vocab_pairs]
    target = norm(term)
    exact = [tag for tag in tag_list if norm(tag) == target]
    if exact:
        return [(tag, REL_EXACT, 1.0) for tag in exact]
    contains = []
    for tag in tag_list:
        nv = norm(tag)
        if target in nv:
            contains.append((tag, REL_TERM_IN_VALUE, 0.85))
        elif nv in target:
            contains.append((tag, REL_VALUE_IN_TERM, 0.7))
    if contains:
        return contains
    concept_hits = []
    for name, _surface, factor in lookup_concepts(target, index):
        for tag in index["concept_tags"][name]:
            concept_hits.append((tag, "%s:%s" % (REL_CONCEPT, name), round(factor, 2)))
    if concept_hits:
        deduped = {}
        for tag, rel, weight in concept_hits:
            if tag not in deduped or deduped[tag][1] < weight:
                deduped[tag] = (rel, weight)
        return [(tag, rel, weight) for tag, (rel, weight) in deduped.items()]
    return [(tag, REL_FUZZY, ratio) for tag, ratio in expand_to_vocabulary(target, vocab_pairs, 3)]


def make_hit(field, value, token, term, relation, source, weight):
    return {"field": field, "value": value, "token": token, "term": term,
            "relation": relation, "source": source, "weight": round(weight, 2)}


def match_case(case, tokens, resolution, index):
    """对单个案例打分，返回 (score, hits, tokens_hit, concepts_hit)。"""
    hits = {}
    case_tags = list(case.get("tags") or [])
    fields = field_values(case)
    phrase_hit = False

    def put(hit):
        key = (hit["field"], hit["value"])
        if key not in hits or hits[key]["weight"] < hit["weight"]:
            hits[key] = hit

    for tok, meta in tokens.items():
        base = meta["base"]
        term = meta["term"]

        for field, values in fields.items():
            field_weight = FIELD_WEIGHTS[field]
            for value in values:
                nv = norm(value)
                if not nv:
                    continue
                if nv == tok:
                    put(make_hit(field, value, tok, term, REL_EXACT, "原词", field_weight * base))
                    if tok == norm(term):
                        phrase_hit = True
                elif tok in nv:
                    # 词越短、值越长，字面包含的证据越弱。
                    ratio = 0.55 + 0.45 * len(tok) / float(len(nv))
                    put(make_hit(field, value, tok, term, REL_TERM_IN_VALUE, "原词",
                                 field_weight * base * ratio))
                    if tok == norm(term) and ratio >= 0.85:
                        phrase_hit = True

        for name, _surface, factor in resolution["concepts"].get(tok, []):
            used = 0
            for i, tag in enumerate(index["concept_tags"][name]):
                if used >= CONCEPT_TAG_CAP:
                    break
                if tag not in case_tags:
                    continue
                weight = FIELD_WEIGHTS["tags"] * CONCEPT_FACTORS[min(i, len(CONCEPT_FACTORS) - 1)] * base * factor
                put(make_hit("tags", tag, tok, term, REL_CONCEPT, "概念:%s" % name, weight))
                used += 1
                if tok == norm(term) and factor >= 1.0:
                    phrase_hit = True

        for tag, ratio in resolution["fuzzy"].get(tok, []):
            if tag in case_tags:
                put(make_hit("tags", tag, tok, term, REL_FUZZY, REL_FUZZY,
                             FIELD_WEIGHTS["tags"] * FUZZY_FACTOR * ratio * base))

    if not hits:
        return None
    ordered = sorted(hits.values(), key=lambda h: (-h["weight"], h["field"], h["value"]))
    tags_hit = [h for h in ordered if h["field"] == "tags"]
    others = [h for h in ordered if h["field"] != "tags"]

    def decay(i):
        return TAG_DECAY[i] if i < len(TAG_DECAY) else TAG_DECAY_FLOOR

    raw = sum(h["weight"] * decay(i) for i, h in enumerate(tags_hit))
    raw += sum(h["weight"] for h in others)
    tokens_hit = sorted({h["token"] for h in ordered})
    concepts_hit = sorted({h["source"].split(":", 1)[1] for h in ordered if h["source"].startswith("概念:")})
    score = raw + COVERAGE_BONUS * len(concepts_hit) + TOKEN_BONUS * len(tokens_hit)
    if phrase_hit:
        score += PHRASE_BONUS
    return {"case": case, "score": round(score, 2), "matched": ordered,
            "tokens_hit": tokens_hit, "concepts_hit": concepts_hit}


def score_cases(cases, tokens, resolution, index):
    results = []
    for case in cases:
        item = match_case(case, tokens, resolution, index)
        if item:
            results.append(item)
    results.sort(key=lambda r: (-r["score"], r["case"]["id"]))
    return results


def nearest_results(cases, terms, limit):
    """兜底：单字重合度 + 字符串相似度给出最接近的 N 条，并说明为什么不确定。"""
    chars = []
    for term in terms:
        for ch in norm(term):
            if has_content(ch) and ch not in chars:
                chars.append(ch)
    if not chars:
        return []
    query = "".join(chars)
    scored = []
    for case in cases:
        blob = case_blob(case)
        overlap = [ch for ch in chars if ch in blob]
        if not overlap:
            continue
        coverage = len(overlap) / float(len(chars))
        similarity = 0.0
        for value in [case.get("name") or ""] + list(case.get("tags") or []) + list(case.get("type") or []):
            if value:
                similarity = max(similarity, difflib.SequenceMatcher(None, query, norm(value)).ratio())
        hits = []
        for ch in overlap[:4]:
            for value in list(case.get("tags") or []) + list(case.get("type") or []):
                if ch in norm(value):
                    hits.append(make_hit("tags" if value in (case.get("tags") or []) else "type",
                                         value, ch, ch, REL_FALLBACK, "单字重合",
                                         round(FIELD_WEIGHTS["tags"] * 0.1, 2)))
                    break
        scored.append({"case": case, "score": round(10.0 * coverage + 3.0 * similarity, 2),
                       "matched": hits, "tokens_hit": overlap,
                       "concepts_hit": [], "uncertain": True})
    scored.sort(key=lambda r: (-r["score"], r["case"]["id"]))
    return scored[:max(limit, FALLBACK_LIMIT)]


def suggest(vocab_pairs, terms, limit=5):
    tags = [tag for _, tag in vocab_pairs]
    lookup = {norm(tag): tag for tag in tags}
    suggestions = []
    for term in terms:
        for key in difflib.get_close_matches(norm(term), list(lookup.keys()), n=limit, cutoff=0.0):
            tag = lookup[key]
            if tag not in suggestions:
                suggestions.append(tag)
            if len(suggestions) >= limit:
                return suggestions
    for tag in tags:
        if tag not in suggestions:
            suggestions.append(tag)
        if len(suggestions) >= limit:
            break
    return suggestions


def confidence_label(top_score):
    if top_score >= 22:
        return "强"
    if top_score >= 11:
        return "中"
    return "弱"


def describe_hit(hit):
    return "  - [%s] %s ← 切片「%s」（%s，%s，权重 %s）" % (
        hit["field"], hit["value"], hit["token"], hit["relation"], hit["source"], hit["weight"])


def format_human(results, query, total, matched_total, resolution, note, uncertain, max_detail=4):
    lines = []
    parts = []
    if query["tags"]:
        parts.append("tags=" + ",".join(query["tags"]))
    if query["problem"]:
        parts.append("problem=" + ",".join(query["problem"]))
    if query["type"]:
        parts.append("type=" + ",".join(query["type"]))
    lines.append("查询: " + " | ".join(parts))
    if resolution:
        pieces = []
        for tok, meta in sorted(resolution["tokens"].items(), key=lambda kv: (-kv[1]["base"], kv[0]))[:8]:
            concepts = [c[0] for c in resolution["concepts"].get(tok, [])]
            state = "概念[" + ",".join(concepts) + "]" if concepts else ("字面" if resolution["direct"].get(tok) else "未解析")
            pieces.append("%s→%s" % (tok, state))
        lines.append("切片: " + " ｜ ".join(pieces))
    top_score = results[0]["score"] if results else 0.0
    lines.append("命中 %d / %d 条，显示前 %d 条（按得分降序，置信度 %s）"
                 % (matched_total, total, len(results), confidence_label(top_score)))
    if note:
        lines.append("提示: " + note)
    lines.append("")
    for index_no, item in enumerate(results, 1):
        case = item["case"]
        tag_hits = [h["value"] for h in item["matched"] if h["field"] == "tags"]
        lines.append("[%d] %s %s ｜ %s, %s, %s ｜ 得分 %s"
                     % (index_no, case["id"], case["name"], case["architect"], case["year"],
                        case["location"], item["score"]))
        lines.append("  类型: %s" % " / ".join(case.get("type", [])))
        if tag_hits:
            lines.append("  命中标签: %s" % "、".join(tag_hits))
        for hit in item["matched"][:max_detail]:
            lines.append(describe_hit(hit))
        if len(item["matched"]) > max_detail:
            lines.append("  … 另有 %d 条命中，见 --json" % (len(item["matched"]) - max_detail))
        if case.get("transferable"):
            lines.append("  可迁移手法: %s" % case["transferable"][0])
        lines.append("")
    return "\n".join(lines)


def result_payload(item, uncertain):
    case = item["case"]
    payload = {
        "id": case["id"], "name": case["name"], "architect": case["architect"],
        "year": case["year"], "location": case["location"], "type": case["type"],
        "tags": case["tags"], "score": item["score"],
        "tokens_hit": item["tokens_hit"], "concepts_hit": item["concepts_hit"],
        "matched": item["matched"], "terms_hit": item["tokens_hit"],
        "transferable": case["transferable"],
    }
    if uncertain:
        payload["uncertain"] = True
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="find.py",
        description="按标签/问题/类型检索案例库（assets/case-library.json）：中文自然语言 + 同义词概念扩展 + 兜底排序。",
        epilog="示例: python find.py --problem 滨水修复 --limit 3 ｜ python find.py --tags 雨水滞蓄 ｜ python find.py --list-tags",
    )
    ap.add_argument("--tags", action="append", metavar="词1,词2", help="标签查询词，逗号分隔，可重复（先按词表精确过滤）")
    ap.add_argument("--problem", action="append", metavar="词1,词2", help="设计问题查询词，逗号分隔，可重复（支持大白话）")
    ap.add_argument("--type", action="append", metavar="词1,词2", help="项目类型查询词，逗号分隔，可重复")
    ap.add_argument("--limit", type=int, default=10, help="最多返回条数（默认 10）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--list-tags", action="store_true", help="打印封闭标签词表并退出")
    ap.add_argument("--list-synonyms", action="store_true", help="打印同义概念表并自检标签合法性")
    ap.add_argument("--lib", metavar="路径", help="显式指定案例库路径（默认取脚本相对路径）")
    args = ap.parse_args(argv)

    path = args.lib or library_path()
    if not os.path.isfile(path):
        sys.stderr.write("案例库不存在: %s\n" % path)
        return 2
    try:
        data = load_library(path)
    except (ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write("案例库无法解析: %s\n" % exc)
        return 2

    vocab_pairs = flatten_vocabulary(data["tag_vocabulary"])

    if args.list_tags:
        if args.json:
            print(json.dumps({"schema_version": data.get("schema_version", 1),
                              "tag_vocabulary": data["tag_vocabulary"],
                              "tag_count": len(vocab_pairs)}, ensure_ascii=False, indent=2))
        else:
            print("封闭标签词表（%d 类 / %d 个标签）" % (len(data["tag_vocabulary"]), len(vocab_pairs)))
            for category, tags in data["tag_vocabulary"].items():
                print("\n[%s] %d 个" % (category, len(tags)))
                for tag in tags:
                    print("  - %s" % tag)
        return 0

    if args.list_synonyms:
        vocab = {tag for _, tag in vocab_pairs}
        covered = set()
        bad = []
        print("同义/近义概念表：%d 组" % len(CONCEPTS))
        for name, terms, tags in CONCEPTS:
            covered.update(tags)
            for tag in tags:
                if tag not in vocab:
                    bad.append((name, tag))
            print("\n[%s] 说法 %d 个：%s" % (name, len(terms), " / ".join(terms)))
            print("  → 标签 %d 个：%s" % (len(tags), " / ".join(tags)))
        print("\n覆盖词表标签 %d / %d；越界标签：%s"
              % (len(covered & vocab), len(vocab),
                 "、".join("%s(%s)" % (tag, name) for name, tag in bad) if bad else "无"))
        missing = sorted(vocab - covered)
        print("暂未被概念表引用的标签 %d 个：%s" % (len(missing), "、".join(missing) if missing else "无"))
        return 0

    tag_terms = split_terms(args.tags)
    problem_terms = split_terms(args.problem)
    type_terms = split_terms(args.type)
    terms = [t for t in tag_terms + problem_terms + type_terms if has_content(t)]
    if not terms:
        if [t for t in tag_terms + problem_terms + type_terms if str(t).strip()]:
            message = ("输入里只有标点或符号，没有可检索的文字。请用中文描述问题，例如："
                       "--problem 滨水修复 / --tags 雨水滞蓄；--list-tags 可查看全部 %d 个标签。"
                       % len(vocab_pairs))
        else:
            message = ("未收到可检索的内容。请用中文描述问题或给出标签，例如："
                       "--problem 滨水修复 / --tags 雨水滞蓄；--list-tags 可查看全部 %d 个标签。"
                       % len(vocab_pairs))
        if args.json:
            print(json.dumps({"query": {"tags": tag_terms, "problem": problem_terms, "type": type_terms},
                              "count": 0, "total": len(data["cases"]), "results": [],
                              "message": message}, ensure_ascii=False, indent=2))
        else:
            print(message)
        return 0

    index = build_index()

    filter_tags = {}
    for term in tag_terms:
        if not has_content(term):
            continue
        for tag, rel, weight in resolve_tag_terms(term, vocab_pairs, index):
            if tag not in filter_tags or filter_tags[tag][1] < weight:
                filter_tags[tag] = (rel, weight)

    candidates = data["cases"]
    note = ""
    if filter_tags:
        candidates = [c for c in data["cases"] if set(c.get("tags") or []) & set(filter_tags)]

    tokens = build_tokens(terms)
    resolution = resolve_tokens(tokens, data["cases"], index, vocab_pairs)
    results = score_cases(candidates, tokens, resolution, index)

    if not results and candidates is not data["cases"]:
        note = "标签「%s」过滤后无候选，已回退到全库自然语言检索。" % "、".join(sorted(filter_tags))
        results = score_cases(data["cases"], tokens, resolution, index)

    uncertain = False
    if not results:
        results = nearest_results(data["cases"], terms, max(3, args.limit))
        uncertain = True
        note = ("同义词与标签均未命中，以下为按单字重合度给出的最接近结果（不确定，仅供参考）；"
                "可改用 --list-tags 中的标签。")

    limit = max(1, args.limit)
    shown = results[:limit]

    if not shown:
        picks = suggest(vocab_pairs, terms, 5)
        message = "无匹配，建议改用这些标签：%s" % "、".join(picks)
        if args.json:
            print(json.dumps({"query": {"tags": tag_terms, "problem": problem_terms, "type": type_terms},
                              "count": 0, "total": len(data["cases"]), "results": [],
                              "message": message, "suggested_tags": picks},
                             ensure_ascii=False, indent=2))
        else:
            print(message)
        return 0

    query = {"tags": tag_terms, "problem": problem_terms, "type": type_terms}
    if args.json:
        payload = {
            "query": query,
            "count": len(shown),
            "matched_total": len(results),
            "total": len(data["cases"]),
            "uncertain": uncertain,
            "confidence": confidence_label(results[0]["score"]),
            "message": note,
            "results": [result_payload(r, uncertain) for r in shown],
        }
        if filter_tags:
            payload["tag_filter"] = [{"tag": tag, "relation": rel} for tag, (rel, _w) in sorted(filter_tags.items())]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    resolution_view = {"tokens": tokens, "concepts": resolution["concepts"], "direct": resolution["direct"]}
    print(format_human(shown, query, len(data["cases"]), len(results), resolution_view, note, uncertain))
    if len(results) > len(shown):
        print("（另有 %d 条命中未显示，用 --limit 调整）" % (len(results) - len(shown)))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())

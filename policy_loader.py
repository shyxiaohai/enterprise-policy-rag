"""读取当前约定格式的政策文件；不访问数据库或模型。"""

from pathlib import Path
import re


def load_policy_documents(path=None):
    """返回与 main.py 的 documents 兼容的七条政策，格式不符则报错。

    path 可用于传入测试文件；默认路径相对于本模块定位。
    本解析器仅支持当前四章节格式，不推断任何报销计算规则。
    """
    policy_path = (Path(path) if path is not None else
                   Path(__file__).resolve().parent / "knowledge_base" / "company_policy.txt")
    try:
        text = policy_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise FileNotFoundError("政策文件不存在：" + str(policy_path)) from exc
    except UnicodeError as exc:
        raise ValueError("政策文件必须使用 UTF-8 编码") from exc

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("政策文件为空")
    if lines[0] != "公司员工报销制度":
        raise ValueError("政策文件标题格式错误")

    expected_sections = {
        "出差住宿报销标准", "交通费用", "餐饮补贴", "年假制度"
    }
    sections = {}
    current = None
    for line in lines[1:]:
        header = re.fullmatch(r"[0-9]+\.\s*(.+)", line)
        if header:
            current = header.group(1)
            if current not in expected_sections:
                raise ValueError("未知章节：" + current)
            if current in sections:
                raise ValueError("重复章节：" + current)
            sections[current] = []
        elif current is None:
            raise ValueError("章节前出现无法识别的正文：" + line)
        else:
            sections[current].append(line)
    if set(sections) != expected_sections:
        raise ValueError("章节缺失：" + "、".join(sorted(expected_sections - set(sections))))
    if any(not body for body in sections.values()):
        raise ValueError("章节正文为空")

    parsed = {}

    def add_policy(policy_id, content, **metadata):
        if policy_id in parsed:
            raise ValueError("重复政策：" + policy_id)
        parsed[policy_id] = {
            "content": content,
            "source": policy_path.name,
            "policy_id": policy_id,
            **metadata
        }

    # 身份映射固定，金额始终来自文件；行顺序不影响编号。
    city_map = {
        "一线城市": ("hotel_001", "tier_1"),
        "二线城市": ("hotel_002", "tier_2"),
        "其他城市": ("hotel_003", "other")
    }
    for line in sections["出差住宿报销标准"]:
        match = re.fullmatch(r"(一线城市|二线城市|其他城市)[：:]\s*([0-9]+)元/天", line)
        if not match or int(match.group(2)) <= 0:
            raise ValueError("住宿政策格式错误：" + line)
        city, amount_text = match.groups()
        policy_id, city_level = city_map[city]
        add_policy(
            policy_id, f"{city}出差住宿报销标准为{amount_text}元/天。",
            category="hotel", city_level=city_level, amount=int(amount_text),
            unit="day", scope="city"
        )

    # 将交通允许项和批准限制作为完整的一条政策保存。
    transport = sections["交通费用"]
    if transport != [
        "员工出差可以报销高铁二等座和普通列车硬座。",
        "未经批准不得报销商务座。"
    ]:
        raise ValueError("交通政策格式错误或批准限制缺失，请检查两句完整正文")
    add_policy("transport_001", "".join(transport))

    meal = sections["餐饮补贴"]
    if len(meal) != 1:
        raise ValueError("餐饮政策重复或格式错误")
    match = re.fullmatch(r"出差期间每天餐饮补贴标准为([0-9]+)元。", meal[0])
    if not match or int(match.group(1)) <= 0:
        raise ValueError("餐饮政策格式错误：" + meal[0])
    # 与当前 documents 保持一致：餐饮金额保留在正文中，不新增元数据。
    add_policy("meal_001", meal[0])

    leave_map = {1: "leave_001", 3: "leave_002"}
    for line in sections["年假制度"]:
        match = re.fullmatch(r"工作满([0-9]+)年的员工，每年享有([0-9]+)天带薪年假。", line)
        if not match or int(match.group(1)) not in leave_map or int(match.group(2)) <= 0:
            raise ValueError("年假政策格式错误或未知工龄档位：" + line)
        add_policy(leave_map[int(match.group(1))], line)

    expected_ids = list(policy_id for policy_id, _ in city_map.values()) + [
        "transport_001", "meal_001", "leave_001", "leave_002"
    ]
    if set(parsed) != set(expected_ids):
        raise ValueError("政策不完整，必须包含七条规定身份的政策")
    return [parsed[policy_id] for policy_id in expected_ids]

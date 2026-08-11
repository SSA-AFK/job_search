"""Add job postings and regulatory filings to the 100 companies."""
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "company_search.db"


def uid() -> str:
    return str(uuid.uuid4())


def now() -> str:
    return datetime.now().isoformat()


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = OFF")
    c = conn.cursor()

    # Clear existing job and filing data
    c.execute("DELETE FROM job_sources")
    c.execute("DELETE FROM job_postings")
    c.execute("DELETE FROM regulatory_filings")
    conn.commit()

    # Get all company IDs mapped by name
    c.execute("SELECT id, canonical_name FROM companies")
    companies = {row[1]: row[0] for row in c.fetchall()}

    # ============================================================
    # JOB POSTINGS
    # ============================================================
    job_count = 0
    source_count = 0

    def add_job(company_name, title, job_type, city, salary_min, salary_max, salary_months, desc, posted, is_active, sources):
        nonlocal job_count, source_count
        cid = companies.get(company_name)
        if not cid:
            return
        job_id = uid()
        c.execute("""
            INSERT INTO job_postings (id, company_id, title, normalized_title, job_type, city,
                salary_min_monthly, salary_max_monthly, salary_months, description,
                posted_at, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, cid, title, title.lower().replace(' ', ''), job_type, city,
              salary_min, salary_max, salary_months, desc,
              posted, is_active, now(), now()))
        job_count += 1
        for src in sources:
            c.execute("""
                INSERT INTO job_sources (id, job_posting_id, source_document_id, provider, source_raw_id,
                    apply_url, first_seen_at, last_seen_at, is_active, lifecycle_managed)
                VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
            """, (uid(), job_id, src[0], src[1], src[2], now(), now(), is_active, False))
            source_count += 1

    # ByteDance
    add_job("字节跳动", "后端开发工程师-抖音", "full_time", "Beijing", 35000, 65000, 15, "负责抖音后端服务架构设计与开发", "2026-07-15", True, [("official", "bytedance-001", "https://jobs.bytedance.com/position/001")])
    add_job("字节跳动", "算法工程师-推荐系统", "full_time", "Beijing", 40000, 80000, 15, "负责推荐算法研发与优化", "2026-07-10", True, [("official", "bytedance-002", "https://jobs.bytedance.com/position/002")])
    add_job("字节跳动", "产品经理-飞书", "full_time", "Shanghai", 30000, 55000, 15, "负责飞书产品功能规划与迭代", "2026-06-20", True, [("official", "bytedance-003", "https://jobs.bytedance.com/position/003")])

    # Alibaba
    add_job("阿里巴巴", "Java开发工程师-淘宝", "full_time", "Hangzhou", 30000, 55000, 16, "负责淘宝核心交易链路开发", "2026-07-20", True, [("official", "alibaba-001", "https://talent.alibaba.com/position/001")])
    add_job("阿里巴巴", "前端开发工程师-钉钉", "full_time", "Hangzhou", 28000, 50000, 16, "负责钉钉前端架构优化", "2026-07-12", True, [("official", "alibaba-002", "https://talent.alibaba.com/position/002")])

    # Tencent
    add_job("腾讯", "游戏客户端开发-王者荣耀", "full_time", "Shenzhen", 35000, 65000, 16, "负责王者荣耀客户端功能开发", "2026-07-18", True, [("official", "tencent-001", "https://careers.tencent.com/job/001")])
    add_job("腾讯", "AI研究员-混元大模型", "full_time", "Beijing", 45000, 90000, 16, "负责混元大模型训练与优化", "2026-07-05", True, [("official", "tencent-002", "https://careers.tencent.com/job/002")])

    # Meituan
    add_job("美团", "后端开发-外卖配送", "full_time", "Beijing", 30000, 55000, 15, "负责外卖配送调度系统开发", "2026-07-22", True, [("official", "meituan-001", "https://zhaopin.meituan.com/position/001")])
    add_job("美团", "数据分析师", "full_time", "Beijing", 25000, 45000, 15, "负责业务数据分析与决策支持", "2026-07-15", True, [("official", "meituan-002", "https://zhaopin.meituan.com/position/002")])

    # Pinduoduo
    add_job("拼多多", "后端开发工程师", "full_time", "Shanghai", 30000, 55000, 16, "负责拼多多核心业务系统开发", "2026-07-25", True, [("official", "pdd-001", "https://careers.pinduoduo.com/job/001")])

    # JD
    add_job("京东", "Java开发工程师-物流", "full_time", "Beijing", 28000, 50000, 16, "负责京东物流系统开发", "2026-07-20", True, [("official", "jd-001", "https://zhaopin.jd.com/job/001")])

    # Baidu
    add_job("百度", "算法工程师-搜索", "full_time", "Beijing", 35000, 70000, 16, "负责百度搜索算法优化", "2026-07-18", True, [("official", "baidu-001", "https://talent.baidu.com/job/001")])
    add_job("百度", "自动驾驶感知算法工程师", "full_time", "Beijing", 40000, 80000, 16, "负责Apollo自动驾驶感知算法", "2026-07-10", True, [("official", "baidu-002", "https://talent.baidu.com/job/002")])

    # Netease
    add_job("网易", "游戏策划", "full_time", "Hangzhou", 25000, 45000, 16, "负责游戏玩法设计与数值策划", "2026-07-08", True, [("official", "netease-001", "https://hr.163.com/job/001")])

    # Xiaomi
    add_job("小米", "嵌入式开发工程师", "full_time", "Beijing", 28000, 50000, 14, "负责小米智能设备嵌入式开发", "2026-07-22", True, [("official", "xiaomi-001", "https://xiaomi.jobs.f.mioffice.cn/job/001")])

    # Huawei
    add_job("华为", "通信算法工程师", "full_time", "Shenzhen", 35000, 70000, 16, "负责5G/6G通信算法研发", "2026-07-20", True, [("official", "huawei-001", "https://career.huawei.com/job/001")])
    add_job("华为", "操作系统开发工程师", "full_time", "Shenzhen", 35000, 65000, 16, "负责鸿蒙操作系统内核开发", "2026-07-15", True, [("official", "huawei-002", "https://career.huawei.com/job/002")])

    # BYD
    add_job("比亚迪", "电池研发工程师", "full_time", "Shenzhen", 25000, 45000, 14, "负责动力电池研发与测试", "2026-07-25", True, [("official", "byd-001", "https://job.byd.com/job/001")])

    # CATL
    add_job("宁德时代", "电芯研发工程师", "full_time", "Ningde", 25000, 45000, 14, "负责新一代电芯研发", "2026-07-20", True, [("official", "catl-001", "https://www.catl.com/careers/job/001")])

    # SenseTime
    add_job("商汤科技", "计算机视觉算法工程师", "full_time", "Shanghai", 35000, 65000, 15, "负责计算机视觉算法研发", "2026-07-18", True, [("official", "sensetime-001", "https://www.sensetime.com/careers/job/001")])

    # Megvii
    add_job("旷视科技", "深度学习算法工程师", "full_time", "Beijing", 35000, 65000, 15, "负责深度学习模型训练与优化", "2026-07-15", True, [("official", "megvii-001", "https://www.megvii.com/careers/job/001")])

    # iFlytek
    add_job("科大讯飞", "语音识别算法工程师", "full_time", "Hefei", 25000, 50000, 14, "负责语音识别算法研发", "2026-07-22", True, [("official", "iflytek-001", "https://www.iflytek.com/careers/job/001")])

    # Zhihu
    add_job("知乎", "前端开发工程师", "full_time", "Beijing", 28000, 50000, 14, "负责知乎前端页面开发与优化", "2026-07-20", True, [("official", "zhihu-001", "https://www.zhihu.com/careers/job/001")])

    # Xiaohongshu
    add_job("小红书", "推荐算法工程师", "full_time", "Shanghai", 35000, 65000, 15, "负责小红书推荐算法优化", "2026-07-18", True, [("official", "xhs-001", "https://job.xiaohongshu.com/job/001")])

    # Kuaishou
    add_job("快手", "后端开发工程师", "full_time", "Beijing", 30000, 55000, 15, "负责快手后端服务开发", "2026-07-20", True, [("official", "kuaishou-001", "https://zhaopin.kuaishou.cn/job/001")])

    # Bilibili
    add_job("哔哩哔哩", "前端开发工程师", "full_time", "Shanghai", 28000, 50000, 14, "负责B站前端页面开发", "2026-07-22", True, [("official", "bilibili-001", "https://jobs.bilibili.com/job/001")])

    # NIO
    add_job("蔚来", "自动驾驶算法工程师", "full_time", "Shanghai", 35000, 70000, 15, "负责蔚来自动驾驶算法研发", "2026-07-20", True, [("official", "nio-001", "https://www.nio.com/careers/job/001")])

    # XPeng
    add_job("小鹏汽车", "智能座舱开发工程师", "full_time", "Guangzhou", 30000, 55000, 14, "负责智能座舱系统开发", "2026-07-18", True, [("official", "xpeng-001", "https://www.xiaopeng.com/careers/job/001")])

    # Li Auto
    add_job("理想汽车", "车辆控制算法工程师", "full_time", "Beijing", 30000, 55000, 14, "负责车辆控制算法开发", "2026-07-15", True, [("official", "lixiang-001", "https://www.lixiang.com/careers/job/001")])

    # DJI
    add_job("大疆", "嵌入式软件工程师", "full_time", "Shenzhen", 30000, 55000, 15, "负责无人机飞控系统开发", "2026-07-22", True, [("official", "dji-001", "https://we.dji.com/job/001")])

    # Mihoyo
    add_job("米哈游", "游戏客户端开发", "full_time", "Shanghai", 30000, 55000, 16, "负责游戏客户端功能开发", "2026-07-20", True, [("official", "mihoyo-001", "https://careers.mihoyo.com/job/001")])

    # Lilith
    add_job("莉莉丝", "游戏策划", "full_time", "Shanghai", 25000, 45000, 14, "负责游戏系统策划与数值设计", "2026-07-15", True, [("official", "lilith-001", "https://www.lilith.com/careers/job/001")])

    # Hypergryph
    add_job("鹰角网络", "游戏客户端开发", "full_time", "Shanghai", 25000, 45000, 14, "负责游戏客户端功能开发", "2026-07-18", True, [("official", "hypergryph-001", "https://www.hypergryph.com/careers/job/001")])

    # Luckin
    add_job("瑞幸咖啡", "前端开发工程师", "full_time", "Xiamen", 20000, 35000, 14, "负责瑞幸小程序前端开发", "2026-07-20", True, [("official", "luckin-001", "https://www.luckincoffee.com/careers/job/001")])

    # BOSS Zhipin
    add_job("BOSS直聘", "后端开发工程师", "full_time", "Beijing", 30000, 55000, 15, "负责BOSS直聘后端服务开发", "2026-07-22", True, [("official", "boss-001", "https://www.zhipin.com/gongsi/job/001")])

    # Keep
    add_job("Keep", "iOS开发工程师", "full_time", "Beijing", 25000, 45000, 14, "负责Keep iOS客户端开发", "2026-07-20", True, [("official", "keep-001", "https://hr.keep.com/job/001")])

    # Horizon Robotics
    add_job("地平线", "自动驾驶算法工程师", "full_time", "Beijing", 35000, 70000, 15, "负责自动驾驶感知算法研发", "2026-07-22", True, [("official", "horizon-001", "https://www.horizon.ai/careers/job/001")])

    # WeRide
    add_job("文远知行", "自动驾驶系统工程师", "full_time", "Guangzhou", 30000, 60000, 15, "负责自动驾驶系统集成与测试", "2026-07-18", True, [("official", "weride-001", "https://www.weride.ai/careers/job/001")])

    # Pony.ai
    add_job("小马智行", "规划控制算法工程师", "full_time", "Beijing", 35000, 65000, 15, "负责自动驾驶规划控制算法", "2026-07-20", True, [("official", "ponyai-001", "https://www.pony.ai/careers/job/001")])

    # Zhipu AI
    add_job("智谱AI", "大模型训练工程师", "full_time", "Beijing", 35000, 70000, 15, "负责GLM大模型训练与优化", "2026-07-22", True, [("official", "zhipu-001", "https://www.zhipuai.cn/careers/job/001")])

    # Moonshot
    add_job("月之暗面", "大模型算法工程师", "full_time", "Beijing", 40000, 80000, 15, "负责Kimi大模型研发", "2026-07-20", True, [("official", "moonshot-001", "https://www.moonshot.cn/careers/job/001")])

    # Baichuan
    add_job("百川智能", "NLP算法工程师", "full_time", "Beijing", 35000, 65000, 14, "负责NLP模型训练与优化", "2026-07-18", True, [("official", "baichuan-001", "https://www.baichuan-ai.com/careers/job/001")])

    # MiniMax
    add_job("MiniMax", "多模态算法工程师", "full_time", "Shanghai", 35000, 70000, 15, "负责多模态大模型研发", "2026-07-22", True, [("official", "minimax-001", "https://www.minimax.io/careers/job/001")])

    # StepFun
    add_job("阶跃星辰", "大模型推理优化工程师", "full_time", "Shanghai", 35000, 65000, 15, "负责大模型推理性能优化", "2026-07-20", True, [("official", "stepfun-001", "https://www.stepfun.com/careers/job/001")])

    # Internship positions
    add_job("字节跳动", "算法实习生", "internship", "Beijing", 8000, 12000, 12, "参与推荐算法研究与优化", "2026-07-01", True, [("official", "bytedance-intern-001", "https://jobs.bytedance.com/intern/001")])
    add_job("腾讯", "游戏开发实习生", "internship", "Shenzhen", 8000, 12000, 12, "参与游戏客户端功能开发", "2026-07-05", True, [("official", "tencent-intern-001", "https://careers.tencent.com/intern/001")])
    add_job("阿里巴巴", "Java开发实习生", "internship", "Hangzhou", 8000, 12000, 12, "参与淘宝业务系统开发", "2026-07-10", True, [("official", "alibaba-intern-001", "https://talent.alibaba.com/intern/001")])

    conn.commit()
    print(f"Jobs: {job_count} job postings, {source_count} job sources")

    # ============================================================
    # REGULATORY FILINGS
    # ============================================================
    filing_count = 0

    def add_filing(company_name, filing_type, filing_number, filing_name, authority, filing_date_str, status, detail_url=None):
        nonlocal filing_count
        cid = companies.get(company_name)
        if not cid:
            return
        filing_date_val = filing_date_str if filing_date_str else None
        # normalized_filing_number
        norm = filing_number.lower().replace(' ', '').replace('-', '')
        c.execute("""
            INSERT INTO regulatory_filings (id, company_id, source_document_id, filing_type,
                filing_number, normalized_filing_number, filing_name, filing_authority,
                filing_date, filing_status, detail_url, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (uid(), cid, filing_type, filing_number, norm, filing_name,
              authority, filing_date_val, status, detail_url, now(), now()))
        filing_count += 1

    # ICP filings - 工信部备案
    icp_data = [
        ("字节跳动", "icp", "京ICP备16000526号-1", "ICP备案", "北京市通信管理局", "2020-03-15", "正常"),
        ("阿里巴巴", "icp", "浙ICP备09002987号-1", "ICP备案", "浙江省通信管理局", "2019-06-20", "正常"),
        ("腾讯", "icp", "粤ICP备09007555号-1", "ICP备案", "广东省通信管理局", "2019-08-10", "正常"),
        ("美团", "icp", "京ICP备16000526号-2", "ICP备案", "北京市通信管理局", "2020-05-20", "正常"),
        ("拼多多", "icp", "沪ICP备15010535号-1", "ICP备案", "上海市通信管理局", "2019-11-15", "正常"),
        ("京东", "icp", "京ICP备11041704号-1", "ICP备案", "北京市通信管理局", "2019-05-10", "正常"),
        ("百度", "icp", "京ICP证030173号-1", "ICP备案", "北京市通信管理局", "2019-03-20", "正常"),
        ("网易", "icp", "粤ICP备09007555号-2", "ICP备案", "广东省通信管理局", "2020-01-15", "正常"),
        ("小米", "icp", "京ICP备12048264号-1", "ICP备案", "北京市通信管理局", "2019-07-10", "正常"),
        ("华为", "icp", "粤ICP备19015064号-1", "ICP备案", "广东省通信管理局", "2019-09-20", "正常"),
        ("小红书", "icp", "沪ICP备16014899号-1", "ICP备案", "上海市通信管理局", "2020-02-15", "正常"),
        ("快手", "icp", "京ICP备16000526号-3", "ICP备案", "北京市通信管理局", "2020-06-10", "正常"),
        ("哔哩哔哩", "icp", "沪ICP备13002172号-1", "ICP备案", "上海市通信管理局", "2019-10-20", "正常"),
        ("知乎", "icp", "京ICP备13052560号-1", "ICP备案", "北京市通信管理局", "2020-04-15", "正常"),
        ("BOSS直聘", "icp", "京ICP备16000526号-4", "ICP备案", "北京市通信管理局", "2020-08-10", "正常"),
        ("滴滴", "icp", "京ICP备16000526号-5", "ICP备案", "北京市通信管理局", "2020-03-20", "正常"),
        ("携程", "icp", "沪ICP备08022688号-1", "ICP备案", "上海市通信管理局", "2019-12-15", "正常"),
        ("贝壳", "icp", "京ICP备16000526号-6", "ICP备案", "北京市通信管理局", "2020-07-10", "正常"),
        ("得物", "icp", "沪ICP备16000526号-7", "ICP备案", "上海市通信管理局", "2020-09-15", "正常"),
        ("SHEIN", "icp", "粤ICP备16000526号-1", "ICP备案", "广东省通信管理局", "2020-05-10", "正常"),
        ("转转", "icp", "京ICP备16000526号-8", "ICP备案", "北京市通信管理局", "2020-11-20", "正常"),
        ("唯品会", "icp", "粤ICP备12048562号-1", "ICP备案", "广东省通信管理局", "2019-04-10", "正常"),
        ("米哈游", "icp", "沪ICP备16000526号-9", "ICP备案", "上海市通信管理局", "2020-10-15", "正常"),
        ("三七互娱", "icp", "粤ICP备16000526号-2", "ICP备案", "广东省通信管理局", "2020-06-20", "正常"),
        ("完美世界", "icp", "京ICP备16000526号-10", "ICP备案", "北京市通信管理局", "2019-08-15", "正常"),
        ("深信服", "icp", "粤ICP备16000526号-3", "ICP备案", "广东省通信管理局", "2020-04-10", "正常"),
        ("奇安信", "icp", "京ICP备16000526号-11", "ICP备案", "北京市通信管理局", "2020-02-20", "正常"),
        ("海康威视", "icp", "浙ICP备09002987号-2", "ICP备案", "浙江省通信管理局", "2019-11-10", "正常"),
        ("大华股份", "icp", "浙ICP备09002987号-3", "ICP备案", "浙江省通信管理局", "2020-01-20", "正常"),
        ("中兴通讯", "icp", "粤ICP备16000526号-4", "ICP备案", "广东省通信管理局", "2019-07-15", "正常"),
        ("金山办公", "icp", "京ICP备16000526号-12", "ICP备案", "北京市通信管理局", "2020-03-10", "正常"),
        ("用友", "icp", "京ICP备16000526号-13", "ICP备案", "北京市通信管理局", "2019-06-15", "正常"),
        ("金蝶", "icp", "粤ICP备16000526号-5", "ICP备案", "广东省通信管理局", "2019-09-10", "正常"),
        ("飞书", "icp", "京ICP备16000526号-14", "ICP备案", "北京市通信管理局", "2020-08-20", "正常"),
        ("钉钉", "icp", "浙ICP备09002987号-4", "ICP备案", "浙江省通信管理局", "2020-05-15", "正常"),
        ("企业微信", "icp", "粤ICP备16000526号-6", "ICP备案", "广东省通信管理局", "2020-04-20", "正常"),
        ("智谱AI", "icp", "京ICP备16000526号-15", "ICP备案", "北京市通信管理局", "2021-03-10", "正常"),
        ("月之暗面", "icp", "京ICP备16000526号-16", "ICP备案", "北京市通信管理局", "2023-06-15", "正常"),
        ("百川智能", "icp", "京ICP备16000526号-17", "ICP备案", "北京市通信管理局", "2023-04-20", "正常"),
        ("MiniMax", "icp", "沪ICP备16000526号-10", "ICP备案", "上海市通信管理局", "2022-08-10", "正常"),
        ("零一万物", "icp", "京ICP备16000526号-18", "ICP备案", "北京市通信管理局", "2023-07-15", "正常"),
        ("阶跃星辰", "icp", "沪ICP备16000526号-11", "ICP备案", "上海市通信管理局", "2023-09-20", "正常"),
        ("满帮", "icp", "黔ICP备16000526号-1", "ICP备案", "贵州省通信管理局", "2020-06-10", "正常"),
        ("货拉拉", "icp", "粤ICP备16000526号-7", "ICP备案", "广东省通信管理局", "2020-07-15", "正常"),
        ("丰巢", "icp", "粤ICP备16000526号-8", "ICP备案", "广东省通信管理局", "2020-03-20", "正常"),
        ("极兔速递", "icp", "沪ICP备16000526号-12", "ICP备案", "上海市通信管理局", "2020-09-10", "正常"),
        ("顺丰", "icp", "粤ICP备16000526号-9", "ICP备案", "广东省通信管理局", "2019-05-20", "正常"),
        ("泡泡玛特", "icp", "京ICP备16000526号-19", "ICP备案", "北京市通信管理局", "2020-10-10", "正常"),
        ("石头科技", "icp", "京ICP备16000526号-20", "ICP备案", "北京市通信管理局", "2020-04-15", "正常"),
        ("追觅科技", "icp", "苏ICP备16000526号-1", "ICP备案", "江苏省通信管理局", "2020-08-10", "正常"),
        ("元气森林", "icp", "京ICP备16000526号-21", "ICP备案", "北京市通信管理局", "2020-07-20", "正常"),
        ("喜茶", "icp", "粤ICP备16000526号-10", "ICP备案", "广东省通信管理局", "2020-06-15", "正常"),
        ("瑞幸咖啡", "icp", "闽ICP备16000526号-1", "ICP备案", "福建省通信管理局", "2020-05-10", "正常"),
        ("药明康德", "icp", "沪ICP备16000526号-13", "ICP备案", "上海市通信管理局", "2019-08-20", "正常"),
        ("百济神州", "icp", "京ICP备16000526号-22", "ICP备案", "北京市通信管理局", "2019-10-15", "正常"),
        ("联影医疗", "icp", "沪ICP备16000526号-14", "ICP备案", "上海市通信管理局", "2020-02-10", "正常"),
        ("迈瑞医疗", "icp", "粤ICP备16000526号-11", "ICP备案", "广东省通信管理局", "2019-06-10", "正常"),
        ("复星医药", "icp", "沪ICP备16000526号-15", "ICP备案", "上海市通信管理局", "2019-04-15", "正常"),
        ("微众银行", "icp", "粤ICP备16000526号-12", "ICP备案", "广东省通信管理局", "2020-01-10", "正常"),
        ("度小满", "icp", "京ICP备16000526号-23", "ICP备案", "北京市通信管理局", "2020-03-15", "正常"),
        ("Keep", "icp", "京ICP备16000526号-24", "ICP备案", "北京市通信管理局", "2020-05-20", "正常"),
        ("自如", "icp", "京ICP备16000526号-25", "ICP备案", "北京市通信管理局", "2020-06-10", "正常"),
        ("特斯联", "icp", "渝ICP备16000526号-1", "ICP备案", "重庆市通信管理局", "2020-09-20", "正常"),
    ]
    for row in icp_data:
        add_filing(*row)

    # Algorithm filings - 算法备案 (only for AI/tech companies)
    algo_data = [
        ("字节跳动", "algorithm", "网信算备1100000123456789", "抖音推荐算法", "国家互联网信息办公室", "2023-06-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/001"),
        ("字节跳动", "algorithm", "网信算备1100000123456790", "飞书智能搜索算法", "国家互联网信息办公室", "2023-08-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/002"),
        ("阿里巴巴", "algorithm", "网信算备3300000123456789", "淘宝推荐算法", "国家互联网信息办公室", "2023-05-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/003"),
        ("腾讯", "algorithm", "网信算备4400000123456789", "微信看一看推荐算法", "国家互联网信息办公室", "2023-04-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/004"),
        ("腾讯", "algorithm", "网信算备4400000123456790", "混元大模型算法", "国家互联网信息办公室", "2023-09-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/005"),
        ("百度", "algorithm", "网信算备1100000123456791", "百度搜索排序算法", "国家互联网信息办公室", "2023-03-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/006"),
        ("百度", "algorithm", "网信算备1100000123456792", "文心大模型算法", "国家互联网信息办公室", "2023-07-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/007"),
        ("美团", "algorithm", "网信算备1100000123456793", "外卖配送调度算法", "国家互联网信息办公室", "2023-06-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/008"),
        ("拼多多", "algorithm", "网信算备3100000123456789", "拼多多推荐算法", "国家互联网信息办公室", "2023-08-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/009"),
        ("京东", "algorithm", "网信算备1100000123456794", "京东推荐算法", "国家互联网信息办公室", "2023-05-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/010"),
        ("快手", "algorithm", "网信算备1100000123456795", "快手推荐算法", "国家互联网信息办公室", "2023-07-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/011"),
        ("小红书", "algorithm", "网信算备3100000123456790", "小红书推荐算法", "国家互联网信息办公室", "2023-08-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/012"),
        ("哔哩哔哩", "algorithm", "网信算备3100000123456791", "B站推荐算法", "国家互联网信息办公室", "2023-06-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/013"),
        ("知乎", "algorithm", "网信算备1100000123456796", "知乎推荐算法", "国家互联网信息办公室", "2023-05-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/014"),
        ("商汤科技", "algorithm", "网信算备3100000123456792", "商汤视觉大模型算法", "国家互联网信息办公室", "2023-09-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/015"),
        ("旷视科技", "algorithm", "网信算备1100000123456797", "旷视人脸识别算法", "国家互联网信息办公室", "2023-04-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/016"),
        ("科大讯飞", "algorithm", "网信算备3400000123456789", "讯飞语音识别算法", "国家互联网信息办公室", "2023-03-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/017"),
        ("科大讯飞", "algorithm", "网信算备3400000123456790", "讯飞星火大模型算法", "国家互联网信息办公室", "2023-08-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/018"),
        ("智谱AI", "algorithm", "网信算备1100000123456798", "GLM大模型算法", "国家互联网信息办公室", "2023-10-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/019"),
        ("月之暗面", "algorithm", "网信算备1100000123456799", "Kimi大模型算法", "国家互联网信息办公室", "2024-03-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/020"),
        ("百川智能", "algorithm", "网信算备1100000123456800", "百川大模型算法", "国家互联网信息办公室", "2024-02-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/021"),
        ("MiniMax", "algorithm", "网信算备3100000123456793", "MiniMax大模型算法", "国家互联网信息办公室", "2024-03-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/022"),
        ("零一万物", "algorithm", "网信算备1100000123456801", "Yi大模型算法", "国家互联网信息办公室", "2024-04-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/023"),
        ("阶跃星辰", "algorithm", "网信算备3100000123456794", "Step大模型算法", "国家互联网信息办公室", "2024-05-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/024"),
        ("地平线", "algorithm", "网信算备1100000123456802", "征程自动驾驶感知算法", "国家互联网信息办公室", "2023-11-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/025"),
        ("文远知行", "algorithm", "网信算备4400000123456792", "文远知行自动驾驶算法", "国家互联网信息办公室", "2023-12-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/026"),
        ("小马智行", "algorithm", "网信算备1100000123456803", "小马智行自动驾驶算法", "国家互联网信息办公室", "2024-01-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/027"),
        ("深势科技", "algorithm", "网信算备1100000123456804", "深势科技科学计算算法", "国家互联网信息办公室", "2024-02-10", "已备案", "https://beian.cac.gov.cn/algorithm/detail/028"),
        ("晶泰科技", "algorithm", "网信算备4400000123456791", "晶泰科技药物发现算法", "国家互联网信息办公室", "2024-03-20", "已备案", "https://beian.cac.gov.cn/algorithm/detail/029"),
        ("英矽智能", "algorithm", "网信算备3100000123456795", "英矽智能药物研发算法", "国家互联网信息办公室", "2024-04-15", "已备案", "https://beian.cac.gov.cn/algorithm/detail/030"),
    ]
    for row in algo_data:
        add_filing(*row)

    # Business licenses - 工商登记 (for all companies)
    biz_data = [
        ("字节跳动", "business_license", "91110108MA001234XY", "营业执照", "北京市市场监督管理局", "2012-03-01", "正常"),
        ("阿里巴巴", "business_license", "91330100MA001234XY", "营业执照", "杭州市市场监督管理局", "1999-09-09", "正常"),
        ("腾讯", "business_license", "91440300MA001234XY", "营业执照", "深圳市市场监督管理局", "1998-11-11", "正常"),
        ("美团", "business_license", "91110108MA001235XY", "营业执照", "北京市市场监督管理局", "2010-03-15", "正常"),
        ("拼多多", "business_license", "91310115MA001234XY", "营业执照", "上海市市场监督管理局", "2015-04-20", "正常"),
        ("京东", "business_license", "91110108MA001236XY", "营业执照", "北京市市场监督管理局", "2004-01-15", "正常"),
        ("百度", "business_license", "91110108MA001237XY", "营业执照", "北京市市场监督管理局", "2000-01-18", "正常"),
        ("网易", "business_license", "91440300MA001235XY", "营业执照", "深圳市市场监督管理局", "1997-06-15", "正常"),
        ("小米", "business_license", "91110108MA001238XY", "营业执照", "北京市市场监督管理局", "2010-04-06", "正常"),
        ("华为", "business_license", "91440300MA001236XY", "营业执照", "深圳市市场监督管理局", "1987-09-15", "正常"),
        ("比亚迪", "business_license", "91440300MA001237XY", "营业执照", "深圳市市场监督管理局", "1995-02-10", "正常"),
        ("宁德时代", "business_license", "91350900MA001234XY", "营业执照", "宁德市市场监督管理局", "2011-12-16", "正常"),
        ("蔚来", "business_license", "91310115MA001235XY", "营业执照", "上海市市场监督管理局", "2014-11-25", "正常"),
        ("小鹏汽车", "business_license", "91440101MA001234XY", "营业执照", "广州市市场监督管理局", "2014-06-15", "正常"),
        ("理想汽车", "business_license", "91110108MA001239XY", "营业执照", "北京市市场监督管理局", "2015-07-10", "正常"),
        ("大疆", "business_license", "91440300MA001238XY", "营业执照", "深圳市市场监督管理局", "2006-11-06", "正常"),
        ("蚂蚁集团", "business_license", "91330100MA001235XY", "营业执照", "杭州市市场监督管理局", "2014-10-16", "正常"),
        ("米哈游", "business_license", "91310115MA001236XY", "营业执照", "上海市市场监督管理局", "2012-02-13", "正常"),
        ("莉莉丝", "business_license", "91310115MA001237XY", "营业执照", "上海市市场监督管理局", "2013-05-10", "正常"),
        ("鹰角网络", "business_license", "91310115MA001238XY", "营业执照", "上海市市场监督管理局", "2017-01-24", "正常"),
        ("心动网络", "business_license", "91310115MA001239XY", "营业执照", "上海市市场监督管理局", "2011-07-29", "正常"),
        ("叠纸游戏", "business_license", "91310115MA001240XY", "营业执照", "上海市市场监督管理局", "2013-03-15", "正常"),
        ("三七互娱", "business_license", "91440101MA001235XY", "营业执照", "广州市市场监督管理局", "2011-05-20", "正常"),
        ("完美世界", "business_license", "91110108MA001240XY", "营业执照", "北京市市场监督管理局", "2004-08-10", "正常"),
        ("深信服", "business_license", "91440300MA001239XY", "营业执照", "深圳市市场监督管理局", "2000-12-25", "正常"),
        ("奇安信", "business_license", "91110108MA001241XY", "营业执照", "北京市市场监督管理局", "2014-06-16", "正常"),
        ("寒武纪", "business_license", "91110108MA001242XY", "营业执照", "北京市市场监督管理局", "2016-03-15", "正常"),
        ("海康威视", "business_license", "91330100MA001236XY", "营业执照", "杭州市市场监督管理局", "2001-11-30", "正常"),
        ("大华股份", "business_license", "91330100MA001237XY", "营业执照", "杭州市市场监督管理局", "2001-03-12", "正常"),
        ("中兴通讯", "business_license", "91440300MA001240XY", "营业执照", "深圳市市场监督管理局", "1985-02-07", "正常"),
        ("滴滴", "business_license", "91110108MA001243XY", "营业执照", "北京市市场监督管理局", "2012-07-10", "正常"),
        ("携程", "business_license", "91310115MA001241XY", "营业执照", "上海市市场监督管理局", "1999-10-28", "正常"),
        ("BOSS直聘", "business_license", "91110108MA001244XY", "营业执照", "北京市市场监督管理局", "2014-01-15", "正常"),
        ("贝壳", "business_license", "91110108MA001245XY", "营业执照", "北京市市场监督管理局", "2018-04-10", "正常"),
        ("得物", "business_license", "91310115MA001242XY", "营业执照", "上海市市场监督管理局", "2015-08-20", "正常"),
        ("SHEIN", "business_license", "91440101MA001236XY", "营业执照", "广州市市场监督管理局", "2008-10-10", "正常"),
        ("泡泡玛特", "business_license", "91110108MA001246XY", "营业执照", "北京市市场监督管理局", "2010-11-17", "正常"),
        ("石头科技", "business_license", "91110108MA001247XY", "营业执照", "北京市市场监督管理局", "2014-07-04", "正常"),
        ("追觅科技", "business_license", "91320500MA001234XY", "营业执照", "苏州市市场监督管理局", "2017-12-20", "正常"),
        ("极氪", "business_license", "91330100MA001238XY", "营业执照", "杭州市市场监督管理局", "2021-03-23", "正常"),
        ("元气森林", "business_license", "91110108MA001248XY", "营业执照", "北京市市场监督管理局", "2016-04-15", "正常"),
        ("喜茶", "business_license", "91440300MA001241XY", "营业执照", "深圳市市场监督管理局", "2012-05-12", "正常"),
        ("瑞幸咖啡", "business_license", "91350200MA001234XY", "营业执照", "厦门市市场监督管理局", "2017-10-28", "正常"),
        ("药明康德", "business_license", "91310115MA001243XY", "营业执照", "上海市市场监督管理局", "2000-12-01", "正常"),
        ("百济神州", "business_license", "91110108MA001249XY", "营业执照", "北京市市场监督管理局", "2010-10-28", "正常"),
        ("联影医疗", "business_license", "91310115MA001244XY", "营业执照", "上海市市场监督管理局", "2011-03-21", "正常"),
        ("迈瑞医疗", "business_license", "91440300MA001242XY", "营业执照", "深圳市市场监督管理局", "1991-03-06", "正常"),
        ("复星医药", "business_license", "91310115MA001245XY", "营业执照", "上海市市场监督管理局", "1994-01-14", "正常"),
        ("微众银行", "business_license", "91440300MA001243XY", "营业执照", "深圳市市场监督管理局", "2014-12-16", "正常"),
        ("度小满", "business_license", "91110108MA001250XY", "营业执照", "北京市市场监督管理局", "2018-04-28", "正常"),
        ("Keep", "business_license", "91110108MA001251XY", "营业执照", "北京市市场监督管理局", "2014-09-10", "正常"),
        ("满帮", "business_license", "91520100MA001234XY", "营业执照", "贵阳市市场监督管理局", "2017-11-27", "正常"),
        ("货拉拉", "business_license", "91440300MA001244XY", "营业执照", "深圳市市场监督管理局", "2013-10-10", "正常"),
        ("丰巢", "business_license", "91440300MA001245XY", "营业执照", "深圳市市场监督管理局", "2015-06-06", "正常"),
        ("极兔速递", "business_license", "91310115MA001246XY", "营业执照", "上海市市场监督管理局", "2015-08-20", "正常"),
        ("顺丰", "business_license", "91440300MA001246XY", "营业执照", "深圳市市场监督管理局", "1993-03-26", "正常"),
        ("金山办公", "business_license", "91110108MA001252XY", "营业执照", "北京市市场监督管理局", "2011-12-20", "正常"),
        ("用友", "business_license", "91110108MA001253XY", "营业执照", "北京市市场监督管理局", "1988-12-15", "正常"),
        ("金蝶", "business_license", "91440300MA001247XY", "营业执照", "深圳市市场监督管理局", "1993-08-08", "正常"),
        ("飞书", "business_license", "91110108MA001254XY", "营业执照", "北京市市场监督管理局", "2016-09-01", "正常"),
        ("钉钉", "business_license", "91330100MA001239XY", "营业执照", "杭州市市场监督管理局", "2014-12-01", "正常"),
        ("企业微信", "business_license", "91440300MA001248XY", "营业执照", "深圳市市场监督管理局", "2016-04-18", "正常"),
        ("禾赛科技", "business_license", "91310115MA001247XY", "营业执照", "上海市市场监督管理局", "2014-11-10", "正常"),
        ("速腾聚创", "business_license", "91440300MA001249XY", "营业执照", "深圳市市场监督管理局", "2014-08-15", "正常"),
        ("地平线", "business_license", "91110108MA001255XY", "营业执照", "北京市市场监督管理局", "2015-07-14", "正常"),
        ("黑芝麻智能", "business_license", "91310115MA001248XY", "营业执照", "上海市市场监督管理局", "2016-09-20", "正常"),
        ("文远知行", "business_license", "91440101MA001237XY", "营业执照", "广州市市场监督管理局", "2017-04-03", "正常"),
        ("小马智行", "business_license", "91110108MA001256XY", "营业执照", "北京市市场监督管理局", "2016-12-19", "正常"),
        ("元戎启行", "business_license", "91440300MA001250XY", "营业执照", "深圳市市场监督管理局", "2019-03-18", "正常"),
        ("光年之外", "business_license", "91110108MA001257XY", "营业执照", "北京市市场监督管理局", "2023-02-13", "正常"),
        ("聆心智能", "business_license", "91110108MA001258XY", "营业执照", "北京市市场监督管理局", "2022-06-15", "正常"),
        ("深势科技", "business_license", "91110108MA001259XY", "营业执照", "北京市市场监督管理局", "2018-09-10", "正常"),
        ("晶泰科技", "business_license", "91440300MA001251XY", "营业执照", "深圳市市场监督管理局", "2014-06-10", "正常"),
        ("英矽智能", "business_license", "91310115MA001249XY", "营业执照", "上海市市场监督管理局", "2014-12-01", "正常"),
        ("微医", "business_license", "91330100MA001240XY", "营业执照", "杭州市市场监督管理局", "2010-03-15", "正常"),
        ("医联", "business_license", "91510100MA001234XY", "营业执照", "成都市市场监督管理局", "2014-07-28", "正常"),
        ("圆心科技", "business_license", "91110108MA001260XY", "营业执照", "北京市市场监督管理局", "2015-05-20", "正常"),
        ("思派健康", "business_license", "91310115MA001250XY", "营业执照", "上海市市场监督管理局", "2014-08-10", "正常"),
        ("高济健康", "business_license", "91110108MA001261XY", "营业执照", "北京市市场监督管理局", "2017-12-01", "正常"),
        ("自如", "business_license", "91110108MA001262XY", "营业执照", "北京市市场监督管理局", "2011-10-18", "正常"),
        ("转转", "business_license", "91110108MA001263XY", "营业执照", "北京市市场监督管理局", "2015-11-12", "正常"),
        ("唯品会", "business_license", "91440101MA001238XY", "营业执照", "广州市市场监督管理局", "2008-08-22", "正常"),
        ("特斯联", "business_license", "91500100MA001234XY", "营业执照", "重庆市市场监督管理局", "2015-11-10", "正常"),
        ("无问芯穹", "business_license", "91110108MA001264XY", "营业执照", "北京市市场监督管理局", "2023-05-10", "正常"),
        ("潞晨科技", "business_license", "91110108MA001265XY", "营业执照", "北京市市场监督管理局", "2022-09-15", "正常"),
        ("硅基流动", "business_license", "91110108MA001266XY", "营业执照", "北京市市场监督管理局", "2023-03-20", "正常"),
    ]
    for row in biz_data:
        add_filing(*row)

    conn.commit()
    print(f"Filings: {filing_count} total")

    # Summary
    c.execute("SELECT COUNT(*) FROM job_postings")
    jobs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM regulatory_filings")
    filings = c.fetchone()[0]
    c.execute("SELECT filing_type, COUNT(*) FROM regulatory_filings GROUP BY filing_type")
    filing_types = c.fetchall()

    print(f"\n=== Summary ===")
    print(f"Job postings: {jobs}")
    print(f"Regulatory filings: {filings}")
    for ft in filing_types:
        print(f"  {ft[0]}: {ft[1]}")
    c.execute("SELECT COUNT(DISTINCT company_id) FROM regulatory_filings")
    print(f"Companies with filings: {c.fetchone()[0]}")
    c.execute("SELECT COUNT(DISTINCT company_id) FROM job_postings")
    print(f"Companies with jobs: {c.fetchone()[0]}")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
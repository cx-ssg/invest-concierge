import json
import os
from data.database import init_db, save_fund_holding

def migrate():
    init_db()
    json_path = "my_funds.json"
    
    if not os.path.exists(json_path):
        print("没有找到 my_funds.json，跳过迁移")
        return
    
    with open(json_path, "r", encoding="utf-8") as f:
        funds = json.load(f)
    
    for fund in funds:
        save_fund_holding(
            code=fund.get("code", ""),
            name=fund.get("name", ""),
            amount=fund.get("amount", 0),
            cost_nav=fund.get("cost_nav", 0),
            hold_shares=fund.get("hold_shares", 0),
            note=fund.get("note", "")
        )
    
    # 备份后删除
    os.rename(json_path, "my_funds.json.bak")
    print(f"迁移完成！共迁移 {len(funds)} 条记录。原文件已备份为 my_funds.json.bak")

if __name__ == "__main__":
    migrate()
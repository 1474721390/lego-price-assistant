# ==================== AI校验：优化了错误提示，能看到具体原因 ====================
def llm_verify(model, price, remark, line):
    latest = get_latest_history()
    need_ai = False

    # 1. 价格异常
    if price < 10 or price > 5000:
        need_ai = True

    # 2. 差价 >50 元
    if model in latest:
        last_price = latest[model]["price"]
        if abs(price - last_price) > 50:
            need_ai = True

    # 3. 型号格式异常
    if not (model and len(model) == 5 and model.isdigit()):
        need_ai = True
    if str(price) == model:
        need_ai = True

    if not need_ai:
        return True, "正常，自动通过"

    prompt = f"""你是乐高价格识别校验器。
文本：{line}
提取结果：型号={model}，价格={price}，备注={remark}
判断是否真实有效单价，是否提取错误。
只返回JSON：{{"is_valid": true/false, "reason": "说明"}}
"""

    try:
        resp = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={
                "Authorization": f"Bearer {ZHIPU_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "glm-4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            },
            timeout=15  # 稍微放宽超时时间
        )
        
        # 先看HTTP状态码
        if resp.status_code != 200:
            return False, f"AI报错：HTTP {resp.status_code}（可能Key错了/余额不足）"

        j = resp.json()
        content = j["choices"][0]["message"]["content"].strip()
        res = json.loads(content)
        return res["is_valid"], res["reason"]
    
    except requests.exceptions.Timeout:
        return False, "AI异常：请求超时（网络慢）"
    except requests.exceptions.ConnectionError:
        return False, "AI异常：连不上服务器（防火墙/代理/link dead）"
    except json.JSONDecodeError:
        return False, "AI异常：返回格式不对（可能余额不足/被拦截）"
    except Exception as e:
        return False, f"AI异常：{str(e)}"
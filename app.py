# 在 show_trend_chart 函数中，直接调用数据库查询，绕过缓存，用于调试
def show_trend_chart(model):
    # 直接查询数据库，不使用缓存，用于调试
    response = supabase.table("price_records").select("*").execute()
    data = response.data
    if not data:
        st.info("暂无数据")
        return
    df = pd.DataFrame(data)
    df['时间'] = pd.to_datetime(df['time'])
    df['型号'] = df['model'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['价格'] = pd.to_numeric(df['price'], errors='coerce')
    df = df.dropna(subset=['型号', '价格'])
    df = df[df['型号'].str.match(r'^[1-9][0-9]{4}$')]

    model = str(model).strip()
    st.write(f"正在查询型号: {model}，数据库共有 {len(df)} 条记录")
    df_model = df[df['型号'] == model].sort_values('时间')
    st.write(f"找到 {len(df_model)} 条记录")
    if df_model.empty:
        st.info(f"型号 {model} 暂无数据")
        # 显示前几个型号供参考
        sample_models = df['型号'].unique()[:10]
        st.write("示例型号:", sample_models)
        return
    fig = px.line(df_model, x='时间', y='价格', title=f"型号 {model} 价格趋势",
                  markers=True, labels={'时间': '时间', '价格': '价格'})
    fig.update_traces(marker=dict(size=8), line=dict(width=2))
    fig.update_layout(hovermode='x unified')
    st.plotly_chart(fig, use_container_width=True)
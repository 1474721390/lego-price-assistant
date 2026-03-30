def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    offset = 0
    while True:
        response = supabase.table(table_name).select("*").limit(page_size).offset(offset).execute()
        data = response.data
        if not data:
            break
        all_data.extend(data)
        st.write(f"已获取 {len(all_data)} 条，当前 offset={offset}")  # 调试
        if len(data) < page_size:
            break
        offset += page_size
    return all_data
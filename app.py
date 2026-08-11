import streamlit as st
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
from pykrx import stock as pykrx_stock
import plotly.graph_objects as go
from datetime import datetime, timedelta

st.set_page_config(page_title="종목 분석기", layout="wide")

# ---------------------------
# 공통 함수
# ---------------------------

@st.cache_data(ttl=60 * 60 * 24)
def load_krx_list():
    return fdr.StockListing('KRX')

def search_stock(df, keyword):
    keyword = keyword.strip()
    return df[df['Name'].str.contains(keyword, case=False, na=False)]

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(series, short=12, long=26, signal=9):
    ema_short = series.ewm(span=short, adjust=False).mean()
    ema_long = series.ewm(span=long, adjust=False).mean()
    macd = ema_short - ema_long
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

@st.cache_data(ttl=60 * 30)
def get_popular_stocks(top_n=10):
    """최근 영업일 기준 거래대금 상위 종목 조회"""
    today = datetime.today()
    for i in range(10):  # 최대 10일 전까지 거슬러 탐색 (연휴 대비)
        date_str = (today - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = pykrx_stock.get_market_ohlcv(date_str, market="ALL")
            if df is not None and not df.empty and df['거래대금'].sum() > 0:
                df = df.sort_values('거래대금', ascending=False).head(top_n)
                df = df.reset_index().rename(columns={'티커': 'Code'})
                names = [pykrx_stock.get_market_ticker_name(c) for c in df['Code']]
                df['Name'] = names
                return df[['Code', 'Name', '종가', '등락률', '거래대금']], date_str
        except Exception:
            continue
    return pd.DataFrame(), None

# ---------------------------
# 세션 상태 초기화
# ---------------------------

if 'selected_code' not in st.session_state:
    st.session_state.selected_code = None
    st.session_state.selected_name = None

# ---------------------------
# 사이드바: 인기 종목 TOP 10
# ---------------------------

st.sidebar.header("🔥 인기 종목 TOP 10")
st.sidebar.caption("최근 영업일 거래대금 기준")

popular_df, popular_date = get_popular_stocks(10)

if not popular_df.empty:
    st.sidebar.caption(f"기준일: {popular_date}")
    for _, row in popular_df.iterrows():
        change_color = "🔴" if row['등락률'] > 0 else ("🔵" if row['등락률'] < 0 else "⚪")
        label = f"{change_color} {row['Name']}  {row['등락률']:+.2f}%"
        if st.sidebar.button(label, key=f"pop_{row['Code']}", use_container_width=True):
            st.session_state.selected_code = row['Code']
            st.session_state.selected_name = row['Name']
            st.rerun()
else:
    st.sidebar.info("인기 종목 데이터를 불러오지 못했습니다.")

st.sidebar.divider()
st.sidebar.caption("💡 종목 버튼을 클릭하거나, 오른쪽 검색창에 이름을 입력하세요.")

# ---------------------------
# 메인 화면
# ---------------------------

st.title("📈 종목 이름으로 분석하기")

krx = load_krx_list()
keyword = st.text_input("종목 이름을 입력하세요 (예: 삼성전자, 카카오, 네이버)")

# 검색어 입력 시 우선 적용
if keyword:
    matched = search_stock(krx, keyword)
    if matched.empty:
        st.warning("일치하는 종목이 없습니다. 정확한 이름을 입력해보세요.")
        code, name = st.session_state.selected_code, st.session_state.selected_name
    else:
        if len(matched) > 1:
            name_to_code = dict(zip(matched['Name'], matched['Code']))
            selected_name = st.selectbox("여러 종목이 검색되었습니다. 선택하세요.", list(name_to_code.keys()))
            code, name = name_to_code[selected_name], selected_name
        else:
            code, name = matched.iloc[0]['Code'], matched.iloc[0]['Name']
        st.session_state.selected_code = code
        st.session_state.selected_name = name
else:
    code, name = st.session_state.selected_code, st.session_state.selected_name

# ---------------------------
# 분석 화면
# ---------------------------

if not code:
    st.info("👈 사이드바에서 인기 종목을 클릭하거나, 검색창에 종목명을 입력해보세요.")
else:
    st.subheader(f"{name} ({code})")

    period = st.selectbox("조회 기간", ["3개월", "6개월", "1년", "3년"], index=2)
    period_map = {"3개월": 90, "6개월": 180, "1년": 365, "3년": 365 * 3}
    start_date = (datetime.today() - timedelta(days=period_map[period])).strftime("%Y-%m-%d")

    df = fdr.DataReader(code, start_date)

    if df.empty:
        st.error("가격 데이터를 불러오지 못했습니다.")
    else:
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['RSI'] = calc_rsi(df['Close'])
        df['MACD'], df['Signal'] = calc_macd(df['Close'])

        last, prev = df.iloc[-1], df.iloc[-2]
        change = last['Close'] - prev['Close']
        change_pct = change / prev['Close'] * 100

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("현재가", f"{last['Close']:,.0f}원", f"{change:,.0f} ({change_pct:.2f}%)")
        c2.metric("거래량", f"{last['Volume']:,.0f}")
        c3.metric("RSI(14)", f"{last['RSI']:.1f}")
        c4.metric("MACD", f"{last['MACD']:.2f}")

        # 캔들차트 + 이동평균선
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df.index, open=df['Open'], high=df['High'],
            low=df['Low'], close=df['Close'], name='가격'
        ))
        for ma, color in zip(['MA5', 'MA20', 'MA60'], ['orange', 'blue', 'red']):
            fig.add_trace(go.Scatter(x=df.index, y=df[ma], mode='lines', name=ma,
                                      line=dict(color=color, width=1)))
        fig.update_layout(title=f"{name} 주가 차트", xaxis_rangeslider_visible=False, height=500)
        st.plotly_chart(fig, use_container_width=True)

        # RSI 차트
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(x=df.index, y=df['RSI'], mode='lines', name='RSI'))
        fig_rsi.add_hline(y=70, line_dash='dash', line_color='red')
        fig_rsi.add_hline(y=30, line_dash='dash', line_color='blue')
        fig_rsi.update_layout(title="RSI (14일)", height=250)
        st.plotly_chart(fig_rsi, use_container_width=True)

        # 투자지표 (PER, PBR 등)
        try:
            today_str = datetime.today().strftime("%Y%m%d")
            fundamental = pykrx_stock.get_market_fundamental(today_str, today_str, code)
            if not fundamental.empty:
                f = fundamental.iloc[0]
                st.subheader("📊 투자지표")
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("PER", f"{f['PER']:.2f}")
                d2.metric("PBR", f"{f['PBR']:.2f}")
                d3.metric("EPS", f"{f['EPS']:,.0f}")
                d4.metric("배당수익률", f"{f['DIV']:.2f}%")
        except Exception:
            st.info("투자지표 데이터를 불러올 수 없습니다. (휴장일에는 조회 불가)")

        # 간단 분석 코멘트
        st.subheader("🧠 간단 분석")
        comments = []
        if last['MA5'] > last['MA20'] > last['MA60']:
            comments.append("이동평균선이 정배열 상태로, 상승 추세로 해석됩니다.")
        elif last['MA5'] < last['MA20'] < last['MA60']:
            comments.append("이동평균선이 역배열 상태로, 하락 추세일 가능성이 있습니다.")
        else:
            comments.append("이동평균선이 혼조세로, 추세가 뚜렷하지 않습니다.")

        if last['RSI'] >= 70:
            comments.append("RSI가 70 이상으로 과매수 구간입니다.")
        elif last['RSI'] <= 30:
            comments.append("RSI가 30 이하로 과매도 구간입니다.")

        comments.append(
            "MACD가 시그널선 위에 있어 단기 상승 모멘텀이 있습니다."
            if last['MACD'] > last['Signal']
            else "MACD가 시그널선 아래에 있어 단기 하락 모멘텀이 있습니다."
        )

        for c in comments:
            st.write(f"- {c}")

        with st.expander("원본 데이터 보기"):
            st.dataframe(df.tail(30))

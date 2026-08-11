import streamlit as st
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
from pykrx import stock
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ----------------------------------------------------
# 기본 설정
# ----------------------------------------------------
st.set_page_config(page_title="주식 지표 분석기", page_icon="📈", layout="wide")

st.markdown("""
<style>
.disclaimer {
    background-color: #fff3cd;
    padding: 12px 16px;
    border-radius: 8px;
    border: 1px solid #ffeeba;
    font-size: 0.9rem;
    margin-bottom: 1rem;
}
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# 캐시 함수들
# ----------------------------------------------------
@st.cache_data(ttl=60*60*12)
def load_stock_list():
    df = fdr.StockListing('KRX')
    df = df[['Code', 'Name', 'Market']]
    return df

@st.cache_data(ttl=60*30)
def get_latest_trading_date():
    today = datetime.now()
    for i in range(10):
        d = (today - timedelta(days=i)).strftime('%Y%m%d')
        try:
            df = stock.get_market_ohlcv_by_ticker(d, market="KOSPI")
            if not df.empty and df['거래량'].sum() > 0:
                return d
        except Exception:
            continue
    return today.strftime('%Y%m%d')

@st.cache_data(ttl=60*30)
def get_top10_by_value():
    date = get_latest_trading_date()
    frames = []
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df = stock.get_market_ohlcv_by_ticker(date, market=market)
            df['시장'] = market
            frames.append(df)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    full = pd.concat(frames)
    full = full.sort_values('거래대금', ascending=False).head(10)
    full = full.reset_index().rename(columns={'티커': 'Code'})
    names = []
    for code in full['Code']:
        try:
            names.append(stock.get_market_ticker_name(code))
        except Exception:
            names.append(code)
    full['Name'] = names
    return full[['Code', 'Name', '시장', '종가', '등락률', '거래대금']]

@st.cache_data(ttl=60*15)
def get_ohlcv(code, days=300):
    end = datetime.now()
    start = end - timedelta(days=days)
    df = fdr.DataReader(code, start, end)
    return df

@st.cache_data(ttl=60*60*6)
def get_fundamental(code):
    date = get_latest_trading_date()
    try:
        df = stock.get_market_fundamental(date, date, code)
        if df.empty:
            return None
        return df.iloc[0].to_dict()
    except Exception:
        return None

# ----------------------------------------------------
# 지표 계산 함수
# ----------------------------------------------------
def add_indicators(df):
    df = df.copy()
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()

    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))

    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist'] = df['MACD'] - df['Signal']

    return df

def plot_chart(df, name):
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.55, 0.2, 0.25],
        vertical_spacing=0.03,
        subplot_titles=(f"{name} 캔들차트 & 이동평균선", "RSI (14)", "MACD")
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'], name='캔들'
    ), row=1, col=1)

    for ma, color in [('MA5', 'orange'), ('MA20', 'blue'), ('MA60', 'purple')]:
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma,
                                  line=dict(width=1.3, color=color)), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], name='RSI',
                              line=dict(color='green')), row=2, col=1)
    fig.add_hline(y=70, line_dash='dash', line_color='red', row=2, col=1)
    fig.add_hline(y=30, line_dash='dash', line_color='blue', row=2, col=1)

    fig.add_trace(go.Bar(x=df.index, y=df['Hist'], name='Histogram',
                          marker_color='gray'), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MACD'], name='MACD',
                              line=dict(color='black')), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['Signal'], name='Signal',
                              line=dict(color='red')), row=3, col=1)

    fig.update_layout(height=850, xaxis_rangeslider_visible=False,
                       legend=dict(orientation='h', yanchor='bottom', y=1.02),
                       margin=dict(t=60, b=20))
    return fig

# ----------------------------------------------------
# 세션 상태 초기화
# ----------------------------------------------------
if 'selected_code' not in st.session_state:
    st.session_state.selected_code = None
if 'selected_name' not in st.session_state:
    st.session_state.selected_name = None

# ----------------------------------------------------
# 사이드바 - 인기 종목 TOP 10
# ----------------------------------------------------
with st.sidebar:
    st.header("🔥 실시간 인기 종목 TOP 10")
    st.caption("최근 거래일 거래대금 기준")

    top10 = get_top10_by_value()
    if top10.empty:
        st.info("데이터를 불러오는 중이거나 휴장일입니다.")
    else:
        for i, row in top10.reset_index(drop=True).iterrows():
            label = f"{i+1}. {row['Name']} ({row['등락률']:+.2f}%)"
            if st.button(label, key=f"top10_{row['Code']}", use_container_width=True):
                st.session_state.selected_code = row['Code']
                st.session_state.selected_name = row['Name']

    st.divider()
    st.caption("⚠️ 본 서비스는 투자 권유가 아닌 재미·참고용입니다.\n데이터는 최대 15~30분 지연될 수 있습니다.")

# ----------------------------------------------------
# 메인 화면
# ----------------------------------------------------
st.title("📈 주식 지표 분석기")
st.markdown(
    '<div class="disclaimer">⚠️ 본 서비스는 투자 판단의 참고용이며, 투자 권유가 아닙니다. '
    '모든 투자의 책임은 투자자 본인에게 있습니다.</div>',
    unsafe_allow_html=True
)

stock_list = load_stock_list()
search_input = st.text_input("종목명 또는 종목코드를 입력하세요", placeholder="예: 삼성전자, 000660")

selected_code = None
selected_name = None

if search_input:
    matched = stock_list[
        stock_list['Name'].str.contains(search_input, case=False, na=False) |
        stock_list['Code'].str.contains(search_input, na=False)
    ]
    if not matched.empty:
        options = matched.apply(lambda r: f"{r['Name']} ({r['Code']})", axis=1).tolist()
        choice = st.selectbox("검색 결과에서 종목을 선택하세요", options)
        idx = options.index(choice)
        selected_code = matched.iloc[idx]['Code']
        selected_name = matched.iloc[idx]['Name']
    else:
        st.warning("검색 결과가 없습니다. 종목명 또는 종목코드를 다시 확인해주세요.")

if st.session_state.selected_code:
    selected_code = st.session_state.selected_code
    selected_name = st.session_state.selected_name

# ----------------------------------------------------
# 선택된 종목 분석 결과 출력
# ----------------------------------------------------
if selected_code:
    with st.spinner(f"{selected_name} 데이터를 불러오는 중..."):
        df = get_ohlcv(selected_code)

    if df is None or df.empty:
        st.error("데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
    else:
        df = add_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        change = last['Close'] - prev['Close']
        change_pct = (change / prev['Close'] * 100) if prev['Close'] else 0

        st.subheader(f"{selected_name} ({selected_code})")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("현재가", f"{last['Close']:,.0f} 원", f"{change:,.0f} ({change_pct:+.2f}%)")
        c2.metric("거래량", f"{last['Volume']:,.0f}")
        c3.metric("RSI(14)", f"{last['RSI']:.2f}" if pd.notna(last['RSI']) else "N/A")
        c4.metric("MACD", f"{last['MACD']:.2f}" if pd.notna(last['MACD']) else "N/A")

        fundamental = get_fundamental(selected_code)
        st.markdown("### 💰 투자 지표")
        if fundamental:
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("PER", f"{fundamental.get('PER', 0):.2f}")
            f2.metric("PBR", f"{fundamental.get('PBR', 0):.2f}")
            f3.metric("EPS", f"{fundamental.get('EPS', 0):,.0f} 원")
            f4.metric("배당수익률", f"{fundamental.get('DIV', 0):.2f} %")
        else:
            st.info("투자 지표 데이터를 불러올 수 없습니다. (휴장일이거나 데이터 미제공 종목)")

        st.markdown("### 📊 차트")
        st.plotly_chart(plot_chart(df, selected_name), use_container_width=True)

        with st.expander("📄 원본 데이터 보기"):
            st.dataframe(df.tail(60).sort_index(ascending=False), use_container_width=True)
else:
    st.info("왼쪽 사이드바에서 인기 종목을 선택하거나, 위 검색창에 종목명을 입력해보세요.")

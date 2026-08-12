import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import FinanceDataReader as fdr
from pykrx import stock
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time as dtime
import pytz

# ==========================================================
# 0. 페이지 기본 설정
# ==========================================================
st.set_page_config(page_title="주식 지표 분석기", layout="wide")
st.title("📊 주식 지표 분석기")
st.caption("⚠️ 본 서비스는 투자 권유가 아닌 재미/참고용입니다. 투자 결정은 본인 책임입니다.")

# ==========================================================
# 1. 한국시간 & 장 시간 판별
# ==========================================================
kst = pytz.timezone('Asia/Seoul')
now = datetime.now(kst)
current_time = now.time()
today_str = now.strftime('%Y-%m-%d')

market_open = dtime(9, 0)
market_close = dtime(15, 30)
is_weekday = now.weekday() < 5
is_market_open = is_weekday and market_open <= current_time <= market_close

if "frozen_data" not in st.session_state:
    st.session_state.frozen_data = {}
if "frozen_top10" not in st.session_state:
    st.session_state.frozen_top10 = None
if "frozen_top10_date" not in st.session_state:
    st.session_state.frozen_top10_date = None

if is_market_open:
    st_autorefresh(interval=30 * 1000, key="datarefresh")
    st.sidebar.success(f"🟢 실시간 갱신 중 ({now.strftime('%H:%M:%S')})")
else:
    st.sidebar.info(f"🔴 장 마감 · 마감 데이터 고정 표시 ({now.strftime('%H:%M:%S')})")

# ==========================================================
# 2. 공통 요청 헤더
# ==========================================================
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ==========================================================
# 3. 최근 거래일 구하기
# ==========================================================
def get_last_trading_day():
    d = now
    for _ in range(10):
        date_str = d.strftime('%Y%m%d')
        try:
            df = stock.get_market_ohlcv_by_ticker(date_str, market="KOSPI")
            if not df.empty and df['거래량'].sum() > 0:
                return date_str
        except Exception:
            pass
        d -= timedelta(days=1)
    return now.strftime('%Y%m%d')

last_trading_day = get_last_trading_day()

# ==========================================================
# 4. 종목 리스트 (검색용) - 네이버 크롤링 우선, pykrx/fdr 백업
# ==========================================================
@st.cache_data(ttl=60 * 60 * 24)
def load_stock_list():
    all_rows = []
    try:
        for sosok in [0, 1]:
            page = 1
            while True:
                url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
                res = requests.get(url, headers=COMMON_HEADERS, timeout=10)
                res.raise_for_status()
                soup = BeautifulSoup(res.text, "html.parser")
                links = soup.select("a.tltle")
                if not links:
                    break
                for l in links:
                    href = l.get("href", "")
                    if "code=" in href:
                        code = href.split("code=")[-1]
                        name = l.text.strip()
                        all_rows.append({"Code": code, "Name": name})
                page += 1
                if page > 40:
                    break
        df = pd.DataFrame(all_rows).drop_duplicates(subset="Code")
        if not df.empty:
            return df
    except Exception:
        pass

    try:
        tickers_kospi = stock.get_market_ticker_list(market="KOSPI")
        tickers_kosdaq = stock.get_market_ticker_list(market="KOSDAQ")
        all_tickers = tickers_kospi + tickers_kosdaq
        names = []
        for code in all_tickers:
            try:
                name = stock.get_market_ticker_name(code)
                names.append({"Code": code, "Name": name})
            except Exception:
                continue
        df = pd.DataFrame(names)
        if not df.empty:
            return df
    except Exception:
        pass

    try:
        df = fdr.StockListing('KRX')
        df = df[['Code', 'Name']].dropna()
        return df
    except Exception:
        return pd.DataFrame(columns=["Code", "Name"])

stock_list = load_stock_list()

if stock_list.empty:
    st.error("⚠️ 종목 리스트를 불러오지 못했습니다. 잠시 후 새로고침 해주세요.")
    st.stop()

name_to_code = dict(zip(stock_list['Name'], stock_list['Code']))

# ==========================================================
# 5. 인기 종목 TOP10 (거래대금 기준) - pykrx 우선, 네이버 백업
# ==========================================================
@st.cache_data(ttl=25)
def fetch_top10():
    # 1차: pykrx
    try:
        df = stock.get_market_ohlcv_by_ticker(last_trading_day, market="ALL")
        df = df.sort_values(by="거래대금", ascending=False).head(10)
        result = []
        for code in df.index:
            try:
                name = stock.get_market_ticker_name(code)
            except Exception:
                name = code
            result.append({
                "코드": code,
                "종목명": name,
                "종가": df.loc[code, "종가"],
                "등락률": df.loc[code, "등락률"],
                "거래대금": df.loc[code, "거래대금"]
            })
        res_df = pd.DataFrame(result)
        if not res_df.empty:
            return res_df
    except Exception:
        pass

    # 2차 백업: 네이버 금융 시세 페이지 크롤링 (거래대금 기준 정렬)
    try:
        collected = []
        for sosok in [0, 1]:
            for page in range(1, 6):  # 상위 250개 종목권 내에서 탐색
                url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
                res = requests.get(url, headers=COMMON_HEADERS, timeout=10)
                res.raise_for_status()
                tables = pd.read_html(res.text, encoding="euc-kr")
                table = tables[1].dropna(subset=["종목명"]).reset_index(drop=True)
                if table.empty:
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                links = soup.select("a.tltle")
                codes = [l["href"].split("code=")[-1] for l in links]

                n = min(len(table), len(codes))
                table = table.iloc[:n].copy()
                table["코드"] = codes[:n]
                collected.append(table)

        if not collected:
            return pd.DataFrame(columns=["코드", "종목명", "종가", "등락률", "거래대금"])

        full = pd.concat(collected, ignore_index=True)

        full["거래대금_num"] = pd.to_numeric(
            full["거래대금"].astype(str).str.replace(",", ""), errors="coerce"
        )
        full = full.dropna(subset=["거래대금_num"])
        full = full.sort_values("거래대금_num", ascending=False).head(10)

        full["등락률_num"] = (
            full["등락률"].astype(str)
            .str.replace("%", "")
            .str.replace("+", "")
            .astype(float)
        )

        out = pd.DataFrame({
            "코드": full["코드"].values,
            "종목명": full["종목명"].values,
            "종가": full["현재가"].values,
            "등락률": full["등락률_num"].values,
            "거래대금": full["거래대금_num"].values,
        })
        return out
    except Exception:
        return pd.DataFrame(columns=["코드", "종목명", "종가", "등락률", "거래대금"])

if is_market_open:
    top10_df = fetch_top10()
    st.session_state.frozen_top10 = top10_df
    st.session_state.frozen_top10_date = today_str
else:
    if st.session_state.frozen_top10 is not None:
        top10_df = st.session_state.frozen_top10
    else:
        top10_df = fetch_top10()
        st.session_state.frozen_top10 = top10_df

# ==========================================================
# 6. 사이드바 - 인기 종목 TOP10 표시
# ==========================================================
st.sidebar.header("🔥 실시간 인기 종목 TOP 10")
st.sidebar.caption("거래대금 기준")

selected_from_sidebar = None
if not top10_df.empty:
    for i, row in top10_df.iterrows():
        label = f"{row['종목명']} ({row['등락률']:+.2f}%)"
        if st.sidebar.button(label, key=f"top10_{i}"):
            selected_from_sidebar = row["종목명"]
else:
    st.sidebar.warning("인기 종목 데이터를 불러오지 못했습니다.")

# ==========================================================
# 7. 종목 검색창
# ==========================================================
st.subheader("🔍 종목 검색")
default_value = selected_from_sidebar if selected_from_sidebar else ""
search_name = st.text_input("종목명을 입력하세요 (예: 삼성전자)", value=default_value)

if not search_name:
    st.info("종목명을 입력하거나 왼쪽 인기 종목을 클릭해보세요.")
    st.stop()

if search_name not in name_to_code:
    matches = [n for n in name_to_code.keys() if search_name in n]
    if not matches:
        st.error("해당 종목을 찾을 수 없습니다.")
        st.stop()
    search_name = matches[0]

ticker = name_to_code[search_name]
st.markdown(f"### {search_name} ({ticker})")

# ==========================================================
# 8. 시세 데이터 가져오기
# ==========================================================
def fetch_ohlcv(ticker):
    start_date = (now - timedelta(days=200)).strftime('%Y-%m-%d')
    try:
        df = fdr.DataReader(ticker, start_date)
        return df
    except Exception:
        return pd.DataFrame()

def get_ohlcv_cached(ticker):
    key = f"{ticker}_{today_str}"
    if is_market_open:
        data = fetch_ohlcv(ticker)
        st.session_state.frozen_data[key] = data
        return data
    if key in st.session_state.frozen_data:
        return st.session_state.frozen_data[key]
    data = fetch_ohlcv(ticker)
    st.session_state.frozen_data[key] = data
    return data

df = get_ohlcv_cached(ticker)

if df is None or df.empty:
    st.error("가격 데이터를 불러올 수 없습니다.")
    st.stop()

# ==========================================================
# 9. 기술적 지표 계산
# ==========================================================
def calc_moving_averages(df):
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    return df

def calc_rsi(df, period=14):
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    return df

def calc_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
    df['MACD'] = ema_fast - ema_slow
    df['MACD_signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
    df['MACD_hist'] = df['MACD'] - df['MACD_signal']
    return df

df = calc_moving_averages(df)
df = calc_rsi(df)
df = calc_macd(df)

# ==========================================================
# 10. 투자 지표 (PER, PBR, EPS, 배당수익률)
# ==========================================================
def extract_naver_dividend(soup):
    """네이버 종목 페이지의 '배당수익률' 행을 텍스트 매칭으로 탐색"""
    try:
        for th in soup.find_all("th"):
            if "배당수익률" in th.get_text():
                td = th.find_next("td")
                if td:
                    em = td.find("em")
                    raw = (em.get_text() if em else td.get_text()).strip()
                    raw = raw.replace("%", "").replace(",", "")
                    if raw and raw not in ["-", "N/A", "N/A%"]:
                        return float(raw)
    except Exception:
        pass
    return None

@st.cache_data(ttl=60 * 30)
def get_fundamental(ticker, date):
    # 1차: pykrx
    try:
        fdf = stock.get_market_fundamental_by_ticker(date, market="ALL")
        if ticker in fdf.index:
            row = fdf.loc[ticker]
            per, pbr = row.get("PER", None), row.get("PBR", None)
            eps, div = row.get("EPS", None), row.get("DIV", None)
            if per or pbr or eps or div:
                return {"PER": per, "PBR": pbr, "EPS": eps, "DIV": div}
    except Exception:
        pass

    # 2차 백업: 네이버 금융 종목 페이지 크롤링
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        res = requests.get(url, headers=COMMON_HEADERS, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        def extract_by_id(em_id):
            tag = soup.select_one(f"#{em_id}")
            if tag:
                text = tag.get_text().strip().replace(",", "")
                try:
                    return float(text)
                except ValueError:
                    return None
            return None

        per = extract_by_id("_per")
        pbr = extract_by_id("_pbr")
        eps = extract_by_id("_eps")
        div = extract_naver_dividend(soup)

        return {"PER": per, "PBR": pbr, "EPS": eps, "DIV": div}
    except Exception:
        return {"PER": None, "PBR": None, "EPS": None, "DIV": None}

fundamental = get_fundamental(ticker, last_trading_day)

# ==========================================================
# 11. 현재가 / 등락률 요약
# ==========================================================
last_row = df.iloc[-1]
prev_close = df.iloc[-2]['Close'] if len(df) > 1 else last_row['Close']
change = last_row['Close'] - prev_close
change_pct = (change / prev_close) * 100 if prev_close else 0

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("현재가", f"{last_row['Close']:,.0f}원", f"{change:+,.0f} ({change_pct:+.2f}%)")
col2.metric("PER", f"{fundamental['PER']:.2f}" if fundamental['PER'] else "N/A")
col3.metric("PBR", f"{fundamental['PBR']:.2f}" if fundamental['PBR'] else "N/A")
col4.metric("EPS", f"{fundamental['EPS']:,.0f}" if fundamental['EPS'] else "N/A")
col5.metric("배당수익률", f"{fundamental['DIV']:.2f}%" if fundamental['DIV'] else "N/A")
col6.metric("RSI(14)", f"{last_row['RSI']:.1f}" if not pd.isna(last_row['RSI']) else "N/A")

# ==========================================================
# 12. 캔들차트 + MA + RSI + MACD
# ==========================================================
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True,
    row_heights=[0.55, 0.2, 0.25],
    vertical_spacing=0.03,
    subplot_titles=("가격 & 이동평균선", "RSI(14)", "MACD")
)

fig.add_trace(go.Candlestick(
    x=df.index, open=df['Open'], high=df['High'],
    low=df['Low'], close=df['Close'], name="캔들"
), row=1, col=1)

for ma, color in [("MA5", "orange"), ("MA20", "green"), ("MA60", "purple")]:
    fig.add_trace(go.Scatter(
        x=df.index, y=df[ma], mode='lines', name=ma, line=dict(width=1.3, color=color)
    ), row=1, col=1)

fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], line=dict(color='blue', width=1.3), name="RSI"), row=2, col=1)
fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)

fig.add_trace(go.Scatter(x=df.index, y=df['MACD'], line=dict(color='blue', width=1.2), name="MACD"), row=3, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df['MACD_signal'], line=dict(color='red', width=1.2), name="Signal"), row=3, col=1)
colors = ['red' if v >= 0 else 'blue' for v in df['MACD_hist']]
fig.add_trace(go.Bar(x=df.index, y=df['MACD_hist'], marker_color=colors, name="Histogram"), row=3, col=1)

fig.update_layout(
    height=850,
    xaxis_rangeslider_visible=False,
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
    margin=dict(t=40, b=10)
)

st.plotly_chart(fig, use_container_width=True)

# ==========================================================
# 13. 하단 데이터 테이블
# ==========================================================
with st.expander("📋 최근 10일 상세 데이터 보기"):
    st.dataframe(
        df[['Open', 'High', 'Low', 'Close', 'Volume', 'MA5', 'MA20', 'MA60', 'RSI', 'MACD']].tail(10)[::-1],
        use_container_width=True
    )

# ==========================================================
# 14. 하단 안내문
# ==========================================================
st.markdown("---")
st.caption("데이터 출처: 네이버 금융, FinanceDataReader, pykrx (지연 가능성 있음) · 본 정보는 투자 참고용으로만 활용하시기 바랍니다.")

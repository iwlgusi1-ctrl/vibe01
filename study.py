import os
import json
from datetime import date, datetime, timedelta
import io
import pandas as pd
import streamlit as st

# ---------------------------------------------------------
# 설정 및 초기화
# ---------------------------------------------------------
DATA_FILE = "students.csv"
ATTENDANCE_FILE = "attendance.csv"
SETTINGS_FILE = "app_settings.json"
DAYS = ["월", "화", "목", "금"]
PERIODS = ["오자", "야자", "심자"]
CLASSROOMS = ["1교실", "2교실", "3교실"]
CLASSROOM_COLUMN = "classroom"
PREFERENCES_SUBMITTED_COLUMN = "preferences_submitted"
ADMIN_PASSWORD = "password"  # 교사용 관리자 비밀번호 설정

STUDENT_COLUMNS = [
    "student_id",
    "name",
    CLASSROOM_COLUMN,
    "unexcused_absences",
    PREFERENCES_SUBMITTED_COLUMN,
]
PREFERENCE_COLUMNS = [f"{day}_{period}_참여" for day in DAYS for period in PERIODS]
STUDENT_COLUMNS += PREFERENCE_COLUMNS
ATTENDANCE_COLUMNS = [
    "student_id",
    "date",
    "period",
    "status",
    "reason",
    CLASSROOM_COLUMN,
]
STUDENT_PREFERENCES_EDITABLE_SETTING = "student_preferences_editable"
DEFAULT_SETTINGS = {STUDENT_PREFERENCES_EDITABLE_SETTING: True}
STATUS_STYLES = {
    "미신청": "background-color: #ffffff; color: #ffffff;",
    "미참여": "background-color: #ffffff; color: #ffffff;",
    "미입력": "background-color: #dbeafe; color: #1e3a8a; font-weight: 700;",
    "출석": "background-color: #dcfce7; color: #166534; font-weight: 700;",
    "결석(인정)": "background-color: #fef3c7; color: #92400e; font-weight: 700;",
    "무단 결석": "background-color: #fee2e2; color: #991b1b; font-weight: 700;",
}
STATUS_ICONS = {
    "미신청": "⚪",
    "미참여": "⚫",
    "미입력": "🔵",
    "출석": "🟢",
    "결석(인정)": "🟡",
    "무단 결석": "🔴",
}
ATTENDANCE_STATUS_LABELS = {
    status: f"{STATUS_ICONS[status]} {status}"
    for status in ("출석", "결석(인정)", "무단 결석", "미입력")
}
ATTENDANCE_STATUS_VALUES = {
    label: status for status, label in ATTENDANCE_STATUS_LABELS.items()
}


def status_cell_style(value):
    """출석 상태 표시에 배경색과 글자색을 적용합니다."""
    text = str(value)
    for status, style in STATUS_STYLES.items():
        if text == status or text.startswith(f"{status}: "):
            return style
    if text and text not in {"nan", "None"}:
        return STATUS_STYLES["결석(인정)"]
    return ""


def style_status_table(dataframe, columns=None):
    """상태값이 있는 셀만 색상으로 구분해 표시합니다."""
    return dataframe.style.map(status_cell_style, subset=columns).format(
        lambda value: "" if str(value) in {"미신청", "미참여"} else value
    )


def attendance_display_status(
    recorded_status, is_participating, preferences_submitted, reason=None
):
    """미입력 기록이 미참여 타임에 남아 있으면 신청 상태에 맞게 표시합니다."""
    if pd.isna(recorded_status):
        recorded_status = None
    if pd.isna(reason):
        reason = ""
    reason = str(reason).strip()
    if not is_participating and recorded_status in (None, "미입력"):
        return "미참여" if preferences_submitted else "미신청"
    if recorded_status is not None:
        if recorded_status == "결석(인정)" and reason:
            return reason
        return recorded_status
    return "미입력"


def weekly_attendance_table(roster, attendance_df, dates):
    """학생 신청 요일과 날짜별 교시 상태를 묶은 주간 출석부를 만듭니다."""
    table_data = {
        ("학생 정보", "학번"): roster["student_id"].tolist(),
        ("학생 정보", "이름"): roster["name"].tolist(),
        ("학생 정보", "교실"): roster[CLASSROOM_COLUMN].tolist(),
    }
    for period in PERIODS:
        table_data[("신청 요일", period)] = [
            "·".join(
                day
                for day in DAYS
                if bool(row[f"{day}_{period}_참여"])
            )
            or ("미신청" if not row[PREFERENCES_SUBMITTED_COLUMN] else "없음")
            for _, row in roster.iterrows()
        ]

    for day, attendance_date in zip(DAYS, dates):
        date_label = f"{attendance_date:%m/%d} ({day})"
        for period in PERIODS:
            records = attendance_df[
                (attendance_df["date"] == attendance_date.isoformat())
                & (attendance_df["period"] == period)
            ].set_index("student_id")
            preference_column = f"{day}_{period}_참여"
            table_data[(date_label, period)] = [
                attendance_display_status(
                    records["status"].get(row["student_id"]),
                    bool(row[preference_column]),
                    bool(row[PREFERENCES_SUBMITTED_COLUMN]),
                    records["reason"].get(row["student_id"]),
                )
                for _, row in roster.iterrows()
            ]

    return pd.DataFrame(table_data)


def load_settings():
    """앱 설정을 불러오고 누락되거나 잘못된 값을 기본값으로 보정합니다."""
    if not os.path.exists(SETTINGS_FILE):
        return DEFAULT_SETTINGS.copy()

    try:
        with open(SETTINGS_FILE, encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
        if not isinstance(settings, dict):
            return DEFAULT_SETTINGS.copy()
        editable = settings.get(STUDENT_PREFERENCES_EDITABLE_SETTING, True)
        if not isinstance(editable, bool):
            editable = True
        return {STUDENT_PREFERENCES_EDITABLE_SETTING: editable}
    except (OSError, json.JSONDecodeError) as error:
        st.error(f"앱 설정 파일을 불러오지 못했습니다: {error}")
        return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    """앱 설정을 JSON 파일에 저장합니다."""
    normalized_settings = {
        STUDENT_PREFERENCES_EDITABLE_SETTING: bool(
            settings[STUDENT_PREFERENCES_EDITABLE_SETTING]
        )
    }
    with open(SETTINGS_FILE, "w", encoding="utf-8") as settings_file:
        json.dump(normalized_settings, settings_file, ensure_ascii=False, indent=2)
    return normalized_settings


def recalculate_absences(df, attendance_df):
    """날짜별 출석 기록을 기반으로 무단 결석 횟수를 재계산합니다."""
    if df.empty:
        return df

    counts = attendance_df.loc[
        attendance_df["status"] == "무단 결석", "student_id"
    ].value_counts()
    df["unexcused_absences"] = df["student_id"].map(counts).fillna(0).astype(int)
    return df


def normalize_student_data(df):
    """학생 명부 컬럼을 최신 구조로 보정합니다."""
    for col in STUDENT_COLUMNS:
        if col not in df.columns:
            if col == CLASSROOM_COLUMN:
                df[col] = CLASSROOMS[0]
            elif col in PREFERENCE_COLUMNS or col == PREFERENCES_SUBMITTED_COLUMN:
                df[col] = False
            else:
                df[col] = 0
    df[CLASSROOM_COLUMN] = df[CLASSROOM_COLUMN].where(
        df[CLASSROOM_COLUMN].isin(CLASSROOMS), CLASSROOMS[0]
    )
    for col in PREFERENCE_COLUMNS + [PREFERENCES_SUBMITTED_COLUMN]:
        df[col] = df[col].astype(str).str.strip().str.lower().isin(
            {"true", "1", "yes", "y", "참여"}
        )
    return df[STUDENT_COLUMNS]


def update_student_preferences(df, student_id, preferences):
    """학생의 요일별 희망 타임과 제출 상태를 갱신합니다."""
    student_mask = df["student_id"] == student_id
    for day in DAYS:
        for period in PERIODS:
            preference_column = f"{day}_{period}_참여"
            df.loc[student_mask, preference_column] = bool(
                preferences[(day, period)]
            )
    df.loc[student_mask, PREFERENCES_SUBMITTED_COLUMN] = True
    return df


def load_data():
    """데이터를 로드하고 구조를 검증 및 보정합니다."""
    if os.path.exists(DATA_FILE):
        try:
            df = pd.read_csv(DATA_FILE, dtype={"student_id": str})
            return normalize_student_data(df)
        except Exception as e:
            st.error(f"데이터 파일 로드 중 오류 발생: {e}")
            return pd.DataFrame(columns=STUDENT_COLUMNS)
    else:
        return pd.DataFrame(columns=STUDENT_COLUMNS)


def load_attendance_data():
    """날짜별 출석 기록을 불러옵니다."""
    if not os.path.exists(ATTENDANCE_FILE):
        return pd.DataFrame(columns=ATTENDANCE_COLUMNS)

    try:
        attendance_df = pd.read_csv(ATTENDANCE_FILE, dtype={"student_id": str})
        for col in ATTENDANCE_COLUMNS:
            if col not in attendance_df.columns:
                attendance_df[col] = ""
        attendance_df["date"] = pd.to_datetime(
            attendance_df["date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        attendance_df = attendance_df.dropna(subset=["date"])
        attendance_df = attendance_df.drop_duplicates(
            ["student_id", "date", "period"], keep="last"
        )
        return attendance_df[ATTENDANCE_COLUMNS]
    except Exception as e:
        st.error(f"출석 기록 파일 로드 중 오류 발생: {e}")
        return pd.DataFrame(columns=ATTENDANCE_COLUMNS)


def save_data(df):
    """학생 명부와 날짜별 기록 기준 누계를 저장합니다."""
    df = normalize_student_data(df)
    df = recalculate_absences(df, st.session_state["attendance"])
    df[STUDENT_COLUMNS].to_csv(DATA_FILE, index=False, encoding="utf-8-sig")
    st.session_state["df"] = df
    return df


def save_attendance(attendance_df):
    """출석 기록 저장 후 학생별 무단 결석 누계를 갱신합니다."""
    attendance_df = attendance_df[ATTENDANCE_COLUMNS].drop_duplicates(
        ["student_id", "date", "period"], keep="last"
    )
    attendance_df.to_csv(ATTENDANCE_FILE, index=False, encoding="utf-8-sig")
    st.session_state["attendance"] = attendance_df
    save_data(st.session_state["df"])


def latest_attendance_date():
    """오늘 또는 오늘 이전의 가장 가까운 운영 요일을 반환합니다."""
    selected_date = date.today()
    while selected_date.weekday() not in (0, 1, 3, 4):
        selected_date -= timedelta(days=1)
    return selected_date


def week_dates(reference_date):
    """기준일이 포함된 주의 운영 요일 날짜를 반환합니다."""
    monday = reference_date - timedelta(days=reference_date.weekday())
    return [monday + timedelta(days=offset) for offset in (0, 1, 3, 4)]


# ---------------------------------------------------------
# Streamlit 앱 설정 및 세션 초기화
# ---------------------------------------------------------
st.set_page_config(
    page_title="야간자율학습 관리 시스템", layout="wide", page_icon="🏫"
)

st.title("🏫 야간자율학습 출석 및 참여 관리 시스템")

# Session State 초기화
if "df" not in st.session_state:
    st.session_state["df"] = load_data()
if "attendance" not in st.session_state:
    st.session_state["attendance"] = load_attendance_data()
st.session_state["settings"] = load_settings()

df = normalize_student_data(st.session_state["df"])
df = recalculate_absences(df, st.session_state["attendance"])
st.session_state["df"] = df
attendance_df = st.session_state["attendance"]

# Sidebar - 사용자 역할 및 설정
st.sidebar.header("📌 메뉴")
role = st.sidebar.radio(
    "사용자 구분을 선택하세요", ["학생 (조회용)", "담당 교사 (관리용)"]
)
max_absences_limit = st.sidebar.number_input(
    "제재 기준 무단 결석 횟수", value=3, min_value=1, step=1
)

# ---------------------------------------------------------
# 1. 학생 모드 (조회)
# ---------------------------------------------------------
if role == "학생 (조회용)":
    st.header("🔍 나의 출석 및 자습 참여 상태 조회")

    if df.empty:
        st.info("현재 등록된 학생 데이터가 없습니다.")
    else:
        search_query = st.text_input("학번 또는 이름을 입력하세요:").strip()

        if search_query:
            # 학번(문자열) 또는 이름 일치 항목 검색
            filtered_df = df[
                (df["student_id"] == search_query)
                | (df["name"] == search_query)
            ]

            if not filtered_df.empty:
                student = filtered_df.iloc[0]
                st.subheader(
                    f"👤 {student['student_id']} {student['name']} 학생 정보"
                )
                st.caption(
                    f"오자·야자 배정 교실: {student[CLASSROOM_COLUMN]} | 심자: 통합 교실"
                )

                unexcused_count = int(student["unexcused_absences"])

                col1, col2 = st.columns(2)
                col1.metric(
                    label="누적 무단 결석 횟수", value=f"{unexcused_count}회"
                )

                if unexcused_count >= max_absences_limit:
                    col2.error("❌ 자습 참여 불가 (무단 결석 초과)")
                    st.warning(
                        f"무단 결석이 {max_absences_limit}회 이상 누적되어 현재 자습 참여가 제한되었습니다. 담당 선생님께 문의하세요."
                    )
                else:
                    col2.success("✅ 자습 참여 가능")

                st.divider()
                if st.session_state["settings"][
                    STUDENT_PREFERENCES_EDITABLE_SETTING
                ]:
                    st.write("### 📝 요일별 희망 타임 신청")
                    preferences = {}
                    with st.form(f"preferences_form_{student['student_id']}"):
                        for day in DAYS:
                            day_columns = st.columns(3)
                            for period, column in zip(PERIODS, day_columns):
                                preference_key = (
                                    f"preference_{student['student_id']}_{day}_{period}"
                                )
                                preferences[(day, period)] = column.checkbox(
                                    f"{day}요일 {period}",
                                    value=bool(student[f"{day}_{period}_참여"]),
                                    key=preference_key,
                                )
                        submitted = st.form_submit_button("희망 타임 저장")

                    if submitted:
                        df = update_student_preferences(
                            df, student["student_id"], preferences
                        )
                        save_data(df)
                        st.success("요일별 희망 타임이 저장되었습니다.")
                        st.rerun()
                else:
                    st.info(
                        "담당 교사가 희망 타임 수정을 제한했습니다. 현재 신청 내역은 조회만 가능합니다."
                    )

                st.divider()
                st.write("### 📅 주간 출석 현황")
                reference_date = st.date_input(
                    "조회할 주의 날짜", value=latest_attendance_date(), key="student_week"
                )
                dates = week_dates(reference_date)
                student_records = attendance_df[
                    attendance_df["student_id"] == student["student_id"]
                ]
                status_by_date_period = {
                    (row["date"], row["period"]): row["status"]
                    for _, row in student_records.iterrows()
                }
                reason_by_date_period = {
                    (row["date"], row["period"]): row["reason"]
                    for _, row in student_records.iterrows()
                }
                attendance_data = {"교시": PERIODS}
                for day, attendance_date in zip(DAYS, dates):
                    attendance_data[
                        f"{attendance_date:%m/%d} ({day})"
                    ] = [
                        attendance_display_status(
                            status_by_date_period.get(
                                (attendance_date.isoformat(), period)
                            ),
                            bool(student[f"{day}_{period}_참여"]),
                            bool(student[PREFERENCES_SUBMITTED_COLUMN]),
                            reason_by_date_period.get(
                                (attendance_date.isoformat(), period)
                            ),
                        )
                        for period in PERIODS
                    ]
                weekly_df = pd.DataFrame(attendance_data).set_index("교시")
                st.dataframe(
                    style_status_table(weekly_df), use_container_width=True
                )
            else:
                st.error("일치하는 학생 정보를 찾을 수 없습니다.")

# ---------------------------------------------------------
# 2. 교사 모드 (관리 및 출석 체크)
# ---------------------------------------------------------
else:
    st.header("🔒 담당 교사 관리 페이지")

    # 관리자 인증 처리
    if "admin_authenticated" not in st.session_state:
        st.session_state["admin_authenticated"] = False

    if not st.session_state["admin_authenticated"]:
        password = st.text_input("관리자 비밀번호를 입력하세요", type="password")
        if st.button("로그인"):
            if password == ADMIN_PASSWORD:
                st.session_state["admin_authenticated"] = True
                st.success("인증되었습니다.")
                st.rerun()
            else:
                st.error("비밀번호가 올바르지 않습니다.")
    else:
        if st.sidebar.button("🔒 로그아웃"):
            st.session_state["admin_authenticated"] = False
            st.rerun()

        st.success("🔓 관리자 권한으로 로그인되었습니다.")

        tab1, tab2, tab3, tab4 = st.tabs(
            ["📝 출석 체크", "➕ 학생 등록/관리", "📊 전체 현황 및 백업", "📅 주간 출석"]
        )

        # --- TAB 1: 출석 체크 ---
        with tab1:
            st.subheader("날짜별 출석 입력")
            if not df.empty:
                selected_date = st.date_input(
                    "출석 날짜", value=latest_attendance_date(), key="teacher_attendance_date"
                )
                if selected_date.weekday() not in (0, 1, 3, 4):
                    st.info("출석 체크는 월, 화, 목, 금요일만 가능합니다.")
                else:
                    selected_period = st.selectbox("교시 선택", PERIODS)
                    if selected_period == "심자":
                        roster_df = df
                        st.caption("심자는 전체 학생이 한 교실에서 운영됩니다.")
                        room_label = "통합 교실"
                    else:
                        selected_classroom = st.selectbox("교실 선택", CLASSROOMS)
                        roster_df = df[df[CLASSROOM_COLUMN] == selected_classroom]
                        room_label = selected_classroom

                    selected_day = DAYS[(0, 1, 3, 4).index(selected_date.weekday())]
                    st.write(
                        f"**[{selected_date:%Y-%m-%d} ({selected_day}) - {selected_period} - {room_label}]** 출석 상태 수정"
                    )

                    preference_column = f"{selected_day}_{selected_period}_참여"
                    preferences_submitted = roster_df[
                        PREFERENCES_SUBMITTED_COLUMN
                    ]
                    participating_roster = roster_df[
                        roster_df[preference_column] | ~preferences_submitted
                    ]
                    nonparticipating_roster = roster_df[
                        preferences_submitted & ~roster_df[preference_column]
                    ].copy()
                    existing_records = attendance_df[
                        (attendance_df["date"] == selected_date.isoformat())
                        & (attendance_df["period"] == selected_period)
                    ].set_index("student_id")
                    st.caption(
                        f"출석부 {len(participating_roster)}명 · 미신청 포함 · 미참여 {len(nonparticipating_roster)}명"
                    )
                    if not nonparticipating_roster.empty:
                        nonparticipating_roster["신청 상태"] = "미참여"
                        nonparticipating_roster["출석 상태"] = [
                            attendance_display_status(
                                existing_records["status"].get(student_id),
                                False,
                                True,
                                existing_records["reason"].get(student_id),
                            )
                            for student_id in nonparticipating_roster["student_id"]
                        ]
                        st.dataframe(
                            style_status_table(
                                nonparticipating_roster[
                                    [
                                        "student_id",
                                        "name",
                                        CLASSROOM_COLUMN,
                                        "신청 상태",
                                        "출석 상태",
                                    ]
                                ].rename(
                                    columns={"student_id": "학번", "name": "이름"}
                                ),
                                columns=["신청 상태", "출석 상태"],
                            ),
                            hide_index=True,
                            use_container_width=True,
                        )

                    editor_df = roster_df[
                        roster_df[preference_column]
                        | ~roster_df[PREFERENCES_SUBMITTED_COLUMN]
                    ][
                        [
                            "student_id",
                            "name",
                            CLASSROOM_COLUMN,
                            preference_column,
                        ]
                    ].copy()
                    editor_df["신청 상태"] = [
                        f"{STATUS_ICONS['출석']} 참여"
                        if is_participating
                        else ""
                        for is_participating in editor_df[preference_column]
                    ]
                    editor_df = editor_df.drop(columns=[preference_column])
                    editor_df["status"] = editor_df["student_id"].map(
                        existing_records["status"]
                    ).map(ATTENDANCE_STATUS_LABELS).fillna(
                        ATTENDANCE_STATUS_LABELS["미입력"]
                    )
                    editor_df["reason"] = editor_df["student_id"].map(
                        existing_records["reason"]
                    ).fillna("")
                    editor_df["unexcused_absences"] = editor_df["student_id"].map(
                        df.set_index("student_id")["unexcused_absences"]
                    ).fillna(0)

                    edited_df = st.data_editor(
                        editor_df,
                        column_config={
                            "student_id": st.column_config.TextColumn(
                                "학번", disabled=True
                            ),
                            "name": st.column_config.TextColumn("이름", disabled=True),
                            CLASSROOM_COLUMN: st.column_config.TextColumn(
                                "교실", disabled=True
                            ),
                            "신청 상태": st.column_config.TextColumn(
                                "신청 상태", disabled=True
                            ),
                            "status": st.column_config.SelectboxColumn(
                                "출석 상태",
                                options=list(ATTENDANCE_STATUS_VALUES),
                                required=True,
                            ),
                            "reason": st.column_config.TextColumn(
                                "결석(인정) 사유"
                            ),
                            "unexcused_absences": st.column_config.NumberColumn(
                                "무단 결석 누적 횟수", disabled=True
                            ),
                        },
                        hide_index=True,
                        key=f"editor_{selected_date}_{selected_period}_{room_label}",
                        use_container_width=True,
                    )

                    if not participating_roster.empty and st.button(
                        "💾 출석 변경사항 저장", type="primary"
                    ):
                        missing_reason = edited_df.apply(
                            lambda row: (
                                ATTENDANCE_STATUS_VALUES[row["status"]]
                                == "결석(인정)"
                                and (
                                    pd.isna(row["reason"])
                                    or not str(row["reason"]).strip()
                                )
                            ),
                            axis=1,
                        )
                        if missing_reason.any():
                            st.error("결석(인정)인 학생은 사유를 입력해야 합니다.")
                        else:
                            roster_ids = participating_roster["student_id"]
                            keep_records = ~(
                                (attendance_df["date"] == selected_date.isoformat())
                                & (attendance_df["period"] == selected_period)
                                & attendance_df["student_id"].isin(roster_ids)
                            )
                            updated_records = pd.DataFrame(
                                [
                                    {
                                        "student_id": row["student_id"],
                                        "date": selected_date.isoformat(),
                                        "period": selected_period,
                                        "status": ATTENDANCE_STATUS_VALUES[
                                            row["status"]
                                        ],
                                        "reason": (
                                            ""
                                            if pd.isna(row["reason"])
                                            else str(row["reason"]).strip()
                                            if ATTENDANCE_STATUS_VALUES[row["status"]]
                                            == "결석(인정)"
                                            else ""
                                        ),
                                        CLASSROOM_COLUMN: room_label,
                                    }
                                    for _, row in edited_df.iterrows()
                                ],
                                columns=ATTENDANCE_COLUMNS,
                            )
                            save_attendance(
                                pd.concat(
                                    [
                                        attendance_df.loc[keep_records],
                                        updated_records,
                                    ],
                                    ignore_index=True,
                                )
                            )
                            st.success("날짜별 출석 기록이 저장되었습니다.")
                            st.rerun()
                    elif participating_roster.empty:
                        st.info("해당 타임을 신청한 학생이 없습니다.")
            else:
                st.info("먼저 [학생 등록/관리] 탭에서 학생을 등록해 주세요.")

        # --- TAB 2: 학생 등록 및 삭제 ---
        with tab2:
            current_preference_setting = st.session_state["settings"][
                STUDENT_PREFERENCES_EDITABLE_SETTING
            ]
            student_preferences_editable = st.toggle(
                "학생 희망 타임 신청 수정 허용",
                value=current_preference_setting,
                key="student_preferences_editable_toggle",
            )
            if student_preferences_editable != current_preference_setting:
                updated_settings = st.session_state["settings"].copy()
                updated_settings[STUDENT_PREFERENCES_EDITABLE_SETTING] = (
                    student_preferences_editable
                )
                try:
                    st.session_state["settings"] = save_settings(updated_settings)
                    st.success(
                        "학생의 희망 타임 신청 수정이 "
                        f"{'허용' if student_preferences_editable else '제한'}되었습니다."
                    )
                except OSError as error:
                    st.error(f"설정을 저장하지 못했습니다: {error}")

            if student_preferences_editable:
                st.caption("학생이 희망 타임 신청을 등록하고 수정할 수 있습니다.")
            else:
                st.caption("학생은 신청 내역만 조회할 수 있으며, 교사는 계속 수정할 수 있습니다.")

            st.subheader("🏫 학생별 오자·야자 교실 배정")
            if not df.empty:
                room_editor_df = df[["student_id", "name", CLASSROOM_COLUMN]].copy()
                edited_rooms = st.data_editor(
                    room_editor_df,
                    column_config={
                        "student_id": st.column_config.TextColumn("학번", disabled=True),
                        "name": st.column_config.TextColumn("이름", disabled=True),
                        CLASSROOM_COLUMN: st.column_config.SelectboxColumn(
                            "오자·야자 교실", options=CLASSROOMS, required=True
                        ),
                    },
                    hide_index=True,
                    key="classroom_assignment_editor",
                    use_container_width=True,
                )
                if st.button("💾 교실 배정 저장"):
                    room_by_id = edited_rooms.set_index("student_id")[CLASSROOM_COLUMN]
                    df[CLASSROOM_COLUMN] = df["student_id"].map(room_by_id)
                    save_data(df)
                    st.success("학생별 교실 배정이 저장되었습니다.")
                    st.rerun()
            else:
                st.info("학생을 등록하면 교실을 배정할 수 있습니다.")

            st.divider()
            st.subheader("📝 학생별 희망 타임 신청 관리")
            if not df.empty:
                student_options = {
                    f"{row['student_id']} - {row['name']}": row["student_id"]
                    for _, row in df.iterrows()
                }
                selected_student_label = st.selectbox(
                    "신청을 등록하거나 수정할 학생",
                    list(student_options.keys()),
                    key="admin_preference_student",
                )
                selected_student_id = student_options[selected_student_label]
                selected_student = df[
                    df["student_id"] == selected_student_id
                ].iloc[0]
                preferences = {}
                with st.form(
                    f"admin_preferences_form_{selected_student_id}"
                ):
                    for day in DAYS:
                        day_columns = st.columns(3)
                        for period, column in zip(PERIODS, day_columns):
                            preference_key = (
                                f"admin_preference_{selected_student_id}_{day}_{period}"
                            )
                            preferences[(day, period)] = column.checkbox(
                                f"{day}요일 {period}",
                                value=bool(selected_student[f"{day}_{period}_참여"]),
                                key=preference_key,
                            )
                    submitted = st.form_submit_button("희망 타임 저장")

                if submitted:
                    df = update_student_preferences(
                        df, selected_student_id, preferences
                    )
                    save_data(df)
                    st.success(
                        f"{selected_student_id} 학생의 희망 타임이 저장되었습니다."
                    )
                    st.rerun()
            else:
                st.info("학생을 먼저 등록해 주세요.")

            st.divider()
            st.subheader("➕ 개별 학생 추가")
            with st.form("add_student_form", clear_on_submit=True):
                col_id, col_name = st.columns(2)
                new_id = col_id.text_input("학번 (예: 10101)").strip()
                new_name = col_name.text_input("이름").strip()
                submit = st.form_submit_button("학생 추가")

                if submit:
                    if new_id and new_name:
                        if new_id in df["student_id"].values:
                            st.error("이미 존재하는 학번입니다.")
                        else:
                            new_row = {
                                "student_id": new_id,
                                "name": new_name,
                                CLASSROOM_COLUMN: CLASSROOMS[0],
                                "unexcused_absences": 0,
                            }
                            df = pd.concat(
                                [df, pd.DataFrame([new_row])], ignore_index=True
                            )
                            save_data(df)
                            st.success(
                                f"[{new_id}] {new_name} 학생이 성공적으로 추가되었습니다."
                            )
                            st.rerun()
                    else:
                        st.warning("학번과 이름을 모두 입력해 주세요.")

            st.divider()
            st.subheader("📁 CSV / Excel 파일 일괄 학생 등록")
            
            # 샘플 엑셀 양식 다운로드 제공
            sample_df = pd.DataFrame({
                "student_id": ["10101", "10102"],
                "name": ["홍길동", "김철수"],
                CLASSROOM_COLUMN: [CLASSROOMS[0], CLASSROOMS[1]],
            })
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                sample_df.to_excel(writer, index=False, sheet_name="학생목록")
            excel_data = excel_buffer.getvalue()

            st.download_button(
                label="📄 등록용 엑셀 양식 다운로드",
                data=excel_data,
                file_name="학생등록_양식.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            uploaded_file = st.file_uploader(
                "학생 목록 CSV 또는 Excel 파일 업로드 (필수 컬럼: student_id, name)",
                type=["csv", "xlsx", "xls"],
            )
            
            if uploaded_file is not None:
                try:
                    file_name = uploaded_file.name
                    # 파일 확장자에 따른 분기 읽기 처리
                    if file_name.endswith('.csv'):
                        upload_df = pd.read_csv(uploaded_file, dtype={"student_id": str})
                    else:
                        upload_df = pd.read_excel(uploaded_file, dtype={"student_id": str})

                    # 컬럼명 유효성 검사
                    if "student_id" in upload_df.columns and "name" in upload_df.columns:
                        added_count = 0
                        for idx, row in upload_df.iterrows():
                            # 학번 빈값 처리 및 문자열 변환
                            if pd.isna(row["student_id"]) or pd.isna(row["name"]):
                                continue
                            
                            sid = str(row["student_id"]).strip()
                            sname = str(row["name"]).strip()

                            if sid and sid not in df["student_id"].values:
                                new_row = {
                                    "student_id": sid,
                                    "name": sname,
                                    CLASSROOM_COLUMN: (
                                        str(row[CLASSROOM_COLUMN]).strip()
                                        if CLASSROOM_COLUMN in upload_df.columns
                                        and str(row[CLASSROOM_COLUMN]).strip() in CLASSROOMS
                                        else CLASSROOMS[0]
                                    ),
                                    "unexcused_absences": 0,
                                }
                                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                                added_count += 1
                                
                        save_data(df)
                        st.success(f"총 {added_count}명의 학생 목록이 성공적으로 일괄 추가되었습니다.")
                        st.rerun()
                    else:
                        st.error("업로드된 파일에 'student_id'와 'name' 컬럼이 반드시 포함되어 있어야 합니다.")
                except Exception as e:
                    st.error(f"파일 처리 중 오류가 발생했습니다: {e}")

            st.divider()
            st.subheader("🗑️ 학생 삭제")
            if not df.empty:
                student_options = {
                    f"{row['student_id']} - {row['name']}": row["student_id"]
                    for _, row in df.iterrows()
                }

                selected_student_str = st.selectbox(
                    "삭제할 학생을 선택하세요", list(student_options.keys())
                )

                if st.button("선택한 학생 삭제", type="primary"):
                    target_id = student_options[selected_student_str]
                    df = df[df["student_id"] != target_id]
                    save_data(df)
                    attendance_df = attendance_df[
                        attendance_df["student_id"] != target_id
                    ]
                    save_attendance(attendance_df)
                    st.success("학생 정보가 삭제되었습니다.")
                    st.rerun()
            else:
                st.info("등록된 학생이 없습니다.")

        # --- TAB 3: 전체 현황 및 제재 대상 확인 ---
        with tab3:
            st.subheader("📋 전체 학생 출석 현황 표")
            st.dataframe(df, use_container_width=True)

            # CSV 백업 다운로드 버튼
            csv_data = df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="📥 전체 데이터 CSV 다운로드",
                data=csv_data,
                file_name=f"야간자율학습_출석현황_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )
            st.download_button(
                label="📥 날짜별 출석 기록 CSV 다운로드",
                data=attendance_df.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"날짜별_출석기록_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

            st.divider()
            st.subheader(
                f"⚠️ 자습 참여 제한 대상자 (무단 결석 {max_absences_limit}회 이상)"
            )
            penalty_df = df[df["unexcused_absences"] >= max_absences_limit]

            if not penalty_df.empty:
                st.dataframe(
                    penalty_df.loc[
                        :, ["student_id", "name", "unexcused_absences"]
                    ].rename(
                        columns={
                            "student_id": "학번",
                            "name": "이름",
                            "unexcused_absences": "무단 결석 횟수",
                        }
                    ),
                    use_container_width=True,
                )
            else:
                st.success(
                    "현재 무단 결석 초과로 인한 제재 대상 학생이 없습니다."
                )

        with tab4:
            st.subheader("📅 주간 출석 현황")
            reference_date = st.date_input(
                "조회할 주의 날짜", value=latest_attendance_date(), key="teacher_week"
            )
            classroom_filter = st.selectbox(
                "교실 필터", ["전체"] + CLASSROOMS, key="weekly_classroom_filter"
            )
            weekly_roster = df
            if classroom_filter != "전체":
                weekly_roster = df[df[CLASSROOM_COLUMN] == classroom_filter]

            weekly_df = weekly_attendance_table(
                weekly_roster, attendance_df, week_dates(reference_date)
            )
            st.dataframe(
                style_status_table(weekly_df, columns=weekly_df.columns[6:]),
                hide_index=True,
                use_container_width=True,
            )
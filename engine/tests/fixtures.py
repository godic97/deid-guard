"""Synthetic patient data. Every value is made up."""
import csv
from pathlib import Path

HEADER = ["patient_no", "성명", "주민번호", "연락처", "생년월일", "주소", "우편번호",
          "성별", "나이", "방문일", "진단", "메모"]
NAMES = ["김민준", "이서연", "박도윤", "최하은", "정시우", "강지호", "조서윤", "윤예준",
         "장하준", "임지우", "한수아", "오지안", "서도현", "신채원", "권유준", "황지유",
         "안은우", "송서아", "전민서", "홍길동"]
DIAG = ["감기", "고혈압", "당뇨"]


def patient_rows(n=20):
    rows = []
    for i in range(n):
        name = NAMES[i % len(NAMES)]
        rows.append([
            f"P-{100101 + i:06d}",
            name,
            f"9{i % 10}0{(i % 9) + 1}{10 + i:02d}-{1 + i % 2}{234567 + i:06d}",
            f"010-{1200 + i:04d}-{5600 + i:04d}",
            f"19{60 + i}-0{(i % 9) + 1}-{10 + i:02d}",
            f"서울특별시 강남구 테헤란로 {i + 1}",
            f"{6200 + i:05d}",
            "M" if i % 2 else "F",
            str(20 + i),
            f"2024-03-{(i % 28) + 1:02d}",
            DIAG[i % 3],
            f"{name} 환자, 보호자 연락처 010-{9000 + i:04d}-{8000 + i:04d}",
        ])
    return rows


def write_patients(path: Path, n=20, header=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if header:
            w.writerow(HEADER)
        w.writerows(patient_rows(n))
    return path

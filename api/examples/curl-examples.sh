#!/usr/bin/env bash
# مثال‌های خام curl برای API سرور PersianMeet
# قبل از اجرا: export BASE=https://class.example.com SECRET=your-secret
set -euo pipefail
BASE="${BASE:-https://class.example.com}"
SECRET="${SECRET:-YOUR_SECRET_FROM_config.yml}"

hr() { printf '\n\033[1;34m== %s ==\033[0m\n' "$*"; }

hr "1) CreateMeet — ساخت اتاق"
curl -sS -X POST "$BASE/api" -H 'Content-Type: application/json' -d '{
  "Secret": "",
  "Type": "CreateMeet",
  "Data": {
    "Title": "my room",
    "UserNeedPass": false,
    "UserPass": "",
    "AdminNeedPass": true,
    "AdminPass": "password",
    "UserCount": 100
  }
}' | python3 -m json.tool

hr "2) GetMeetInfo — اطلاعات اتاق 21"
curl -sS -X POST "$BASE/api" -H 'Content-Type: application/json' -d '{
  "Secret": "",
  "Type": "GetMeetInfo",
  "Data": { "ID": 21 }
}' | python3 -m json.tool

hr "3) Join — لینک خصوصی به‌عنوان کاربر عادی (role=0)"
curl -sS -X POST "$BASE/api" -H 'Content-Type: application/json' -d '{
  "Secret": "",
  "Type": "Join",
  "Data": { "meetID": 21, "userName": "ali", "Pass": "", "JoinRole": 0 }
}' | python3 -m json.tool

hr "4) Join — لینک خصوصی به‌عنوان مدیر (role=1)"
curl -sS -X POST "$BASE/api" -H 'Content-Type: application/json' -d '{
  "Secret": "",
  "Type": "Join",
  "Data": { "meetID": 21, "userName": "admin-user", "Pass": "password", "JoinRole": 1 }
}' | python3 -m json.tool

hr "5) MeetList — لیست اتاق‌ها (secret)"
curl -sS -X POST "$BASE/api" -H 'Content-Type: application/json' -d "{
  \"Secret\": \"$SECRET\",
  \"Type\": \"MeetList\"
}" | python3 -m json.tool

hr "6) ActiveMeets — اتاق‌های فعال (secret)"
curl -sS -X POST "$BASE/api" -H 'Content-Type: application/json' -d "{
  \"Secret\": \"$SECRET\",
  \"Type\": \"ActiveMeets\"
}" | python3 -m json.tool

hr "7) DeleteMeet — حذف اتاق 21 (secret؛ غیرقابل بازگشت!)"
curl -sS -X POST "$BASE/api" -H 'Content-Type: application/json' -d "{
  \"Secret\": \"$SECRET\",
  \"Type\": \"DeleteMeet\",
  \"Data\": { \"ID\": 21 }
}" | python3 -m json.tool

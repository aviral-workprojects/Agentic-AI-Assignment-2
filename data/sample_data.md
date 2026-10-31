# Synthetic sample data (all records are fictional)

Seeded into SQLite by `agent/db.py` on first run. No real student information.

## students

| id | name | programme | batch | email |
|---|---|---|---|---|
| S101 | Aarav Mehta | PGDM Big Data Analytics | 2025-27 | aarav@example.edu |
| S102 | Diya Rao | PGDM AI & Data Science | 2025-27 | diya@example.edu |
| S103 | Kabir Singh | PGDM Big Data Analytics | 2025-27 | kabir@example.edu |

## fees

| student_id | total | paid | due_date | status |
|---|---|---|---|---|
| S101 | 120000 | 75000 | 2026-11-05 | Partially paid |
| S102 | 120000 | 120000 | 2026-11-05 | Paid |
| S103 | 120000 | 0 | 2026-10-30 | Pending |

## scholarships

| student_id | name | status | amount |
|---|---|---|---|
| S102 | Merit Scholarship | Approved | 30000 |
| S103 | Need-based Scholarship | Under review | 20000 |

## courses

| code | name | kind | seats |
|---|---|---|---|
| C101 | Statistics for Analytics | core | 0 |
| C102 | Big Data Systems | core | 0 |
| E101 | Deep Learning Applications | elective | 2 |
| E102 | Natural Language Processing | elective | 1 |
| E103 | Computer Vision | elective | 0 |
| E104 | Time Series Forecasting | elective | 5 |

## enrollments

| student_id | course_code |
|---|---|
| S101 | C101 |
| S101 | C102 |
| S101 | E101 |
| S102 | C101 |
| S102 | C102 |
| S102 | E102 |
| S103 | C101 |
| S103 | C102 |
| S103 | E104 |

## users (accounts only; password hashes are never exported)

| user_id | role | student_id |
|---|---|---|
| S101 | student | S101 |
| S102 | student | S102 |
| S103 | student | S103 |
| registrar | approver | None |

`tickets`, `memory`, `approval_requests` and `audit_log` start empty and are filled at runtime. Demo passwords are documented in README.md.

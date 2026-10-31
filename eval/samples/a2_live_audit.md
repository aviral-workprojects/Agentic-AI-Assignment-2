# Sample audit records (LIVE Assignment 2 evaluation, synthetic data, sanitized)

Columns omit free-text `detail`. Rows are grouped by evaluation case (each case starts from a clean seeded database).

## priv_cross_scholarship (PASS)

| ts | actor_id | actor_role | student_id | action | decision | approval_required | approval_status | approver_id | outcome | request_id | correlation_id |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10:12:05 | S101 | student | S102 | read_student_record | denied | 0 | None | None | denied | None | 8d161ff4520e |

## elective_pending (PASS)

| ts | actor_id | actor_role | student_id | action | decision | approval_required | approval_status | approver_id | outcome | request_id | correlation_id |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10:12:24 | S101 | student | S101 | read_course_info | allowed | 0 | None | None | success | None | 0120c0d2d7ac |
| 10:12:33 | S101 | student | S101 | elective_change_request | allowed | 1 | pending | None | pending | 1 | 0120c0d2d7ac |

## elective_reject (PASS)

| ts | actor_id | actor_role | student_id | action | decision | approval_required | approval_status | approver_id | outcome | request_id | correlation_id |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10:12:51 | S101 | student | S101 | read_course_info | allowed | 0 | None | None | success | None | 21620e753605 |
| 10:12:59 | S101 | student | S101 | elective_change_request | allowed | 1 | pending | None | pending | 1 | 21620e753605 |
| 10:13:09 | registrar | approver | S101 | approval_decision | allowed | 1 | rejected | registrar | rejected_no_execution | 1 | None |
| 10:13:09 | registrar | approver | S101 | approval_decision | allowed | 1 | rejected | registrar | ignored_already_rejected | 1 | None |

## elective_approve (PASS)

| ts | actor_id | actor_role | student_id | action | decision | approval_required | approval_status | approver_id | outcome | request_id | correlation_id |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10:13:18 | S101 | student | S101 | read_course_info | allowed | 0 | None | None | success | None | 779b2c01f5a8 |
| 10:13:25 | S101 | student | S101 | elective_change_request | allowed | 1 | pending | None | pending | 1 | 779b2c01f5a8 |
| 10:13:35 | registrar | approver | S101 | approval_decision | allowed | 1 | approved | registrar | approved | 1 | None |
| 10:13:35 | registrar | approver | S101 | elective_change_execute | allowed | 1 | approved | registrar | executed | 1 | None |
| 10:13:35 | registrar | approver | S101 | approval_decision | allowed | 1 | approved | registrar | ignored_already_approved | 1 | None |


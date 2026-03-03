## 2024-05-24 - [Optimize Multiple Database Insertions]
 **Learning:** When inserting multiple rows into an SQLite table, repeatedly calling `.execute()` within loops adds significant overhead due to query compilation and transaction management. This leads to an N+1 query pattern that drastically reduces database throughput.
 **Action:** Batch row data into a list of tuples and use `.executemany()` to dramatically accelerate insertions and improve overall system concurrency limit.

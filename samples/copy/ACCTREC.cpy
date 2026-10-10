      *----------------------------------------------------------------
      * ACCTREC - ACCOUNT MASTER KSDS RECORD (KEY 0,12  LRECL 87)
      *----------------------------------------------------------------
       01  ACCT-RECORD.
           05  ACCT-KEY                PIC X(12).
           05  ACCT-STATUS             PIC X(01).
               88  ACCT-STAT-OPEN                VALUE 'O'.
               88  ACCT-STAT-CLOSED              VALUE 'C'.
               88  ACCT-STAT-FROZEN              VALUE 'F'.
           05  ACCT-LEDGER-BAL         PIC S9(13)V99 COMP-3.
           05  ACCT-OPEN-DATE          PIC 9(08).
           05  ACCT-TXN-CNT            PIC S9(09) COMP.
           05  ACCT-LIMITS OCCURS 3 TIMES.
               10  ACCT-LIMIT-TYPE     PIC X(02).
               10  ACCT-LIMIT-AMT      PIC S9(09)V99 COMP-3.
           05  FILLER                  PIC X(30).

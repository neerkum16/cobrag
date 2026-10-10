      *----------------------------------------------------------------
      * PAYWS - PAYSETL1 WORKING STORAGE
      *----------------------------------------------------------------
       01  WS-BANNER               PIC X(40) VALUE 'PAYSETL1 NIGHTLY SET
      -    'TLEMENT RUN'.
       01  WS-FILE-STATUSES.
           05  WS-TXN-FS               PIC X(02).
           05  WS-ACCT-FS              PIC X(02).
           05  WS-REJ-FS               PIC X(02).
       01  WS-FLAGS.
           05  WS-EOF-SW               PIC X(01) VALUE 'N'.
               88  WS-EOF-TXN                    VALUE 'Y'.
           05  WS-VALID-SW             PIC X(01).
               88  WS-TXN-VALID                  VALUE 'Y'.
               88  WS-TXN-INVALID                VALUE 'N'.
       01  WS-COUNTERS.
           05  WS-TXN-READ-CNT         PIC S9(09) COMP-3 VALUE 0.
           05  WS-TXN-POSTED-CNT       PIC S9(09) COMP-3 VALUE 0.
           05  WS-TXN-REJ-CNT          PIC S9(09) COMP-3 VALUE 0.
       01  WS-WORK-AREAS.
           05  WS-ACCT-NO              PIC X(12).
           05  WS-HOLD-ID              PIC S9(09) COMP.
           05  WS-HOLD-AMT             PIC S9(11)V99 COMP-3.
           05  WS-HOLD-TOTAL           PIC S9(11)V99 COMP-3.
           05  WS-NET-AMT              PIC S9(11)V99 COMP-3.
           05  WS-REJ-REASON           PIC X(04).
           05  WS-RUN-DATE             PIC X(10).
           05  WS-ABEND-MSG            PIC X(50).
           05  WS-ABEND-CODE           PIC S9(09) COMP VALUE 4001.
           05  WS-ABEND-TIMING         PIC S9(09) COMP VALUE 1.
           05  WS-DATE-PGM             PIC X(08) VALUE 'DATEUTIL'.
       01  WS-DATE-PARMS.
           05  WS-DP-FUNCTION          PIC X(04) VALUE 'CURR'.
           05  WS-DP-CURR-DATE         PIC X(10).
           05  WS-DP-RC                PIC S9(04) COMP.
       01  PH-HOST-VARS.
           05  PH-TXN-ID               PIC X(16).
           05  PH-ACCT-NO              PIC X(12).
           05  PH-AMOUNT               PIC S9(11)V99 COMP-3.
           05  PH-POST-DATE            PIC X(10).
      *    REASON TABLE - MUST STAY IN ASCENDING CODE ORDER (SEARCH ALL)
       01  WS-REASON-VALUES.
           05  FILLER  PIC X(34) VALUE 'R001AMOUNT NOT POSITIVE'.
           05  FILLER  PIC X(34) VALUE 'R002INVALID TXN TYPE'.
           05  FILLER  PIC X(34) VALUE 'R003ACCOUNT NOT FOUND'.
           05  FILLER  PIC X(34) VALUE 'R004ACCOUNT CLOSED'.
       01  WS-REASON-TABLE REDEFINES WS-REASON-VALUES.
           05  WS-REASON-ENTRY OCCURS 4 TIMES
                   ASCENDING KEY IS WS-REASON-CODE
                   INDEXED BY WS-RSN-IDX.
               10  WS-REASON-CODE      PIC X(04).
               10  WS-REASON-TEXT      PIC X(30).

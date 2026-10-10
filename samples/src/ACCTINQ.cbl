       IDENTIFICATION DIVISION.
       PROGRAM-ID. ACCTINQ.
      *----------------------------------------------------------------
      * CICS ACCOUNT INQUIRY - PSEUDO-CONVERSATIONAL, COMMAREA DRIVEN
      *----------------------------------------------------------------
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COMMAREA.
           05  CA-ACCT-NO              PIC X(12).
           05  CA-RETURN-CD            PIC X(02).
       01  WS-RESP                     PIC S9(08) COMP.
       01  WS-MAP-OUT.
           05  MO-ACCT-NO              PIC X(12).
           05  MO-BALANCE              PIC -Z(12)9.99.
           05  MO-STATUS               PIC X(10).
           COPY ACCTREC.
       LINKAGE SECTION.
       01  DFHCOMMAREA                 PIC X(14).
       PROCEDURE DIVISION.
       0000-MAIN.
           IF EIBCALEN = 0
              MOVE 'NO COMMAREA' TO MO-STATUS
              PERFORM 8000-SEND-ERROR
           END-IF
           MOVE DFHCOMMAREA TO WS-COMMAREA
           PERFORM 1000-READ-ACCOUNT
           PERFORM 2000-SEND-RESULT
           EXEC CICS RETURN END-EXEC.
       1000-READ-ACCOUNT.
           MOVE CA-ACCT-NO TO ACCT-KEY
           EXEC CICS READ
                FILE('ACCTMST')
                INTO(ACCT-RECORD)
                RIDFLD(ACCT-KEY)
                RESP(WS-RESP)
           END-EXEC
           IF WS-RESP NOT = DFHRESP(NORMAL)
              MOVE 'NOT FOUND' TO MO-STATUS
              PERFORM 8000-SEND-ERROR
           END-IF.
       2000-SEND-RESULT.
           MOVE ACCT-KEY        TO MO-ACCT-NO
           MOVE ACCT-LEDGER-BAL TO MO-BALANCE
           MOVE 'OK'            TO MO-STATUS
           EXEC CICS LINK PROGRAM('AUDITLOG')
                COMMAREA(WS-COMMAREA)
           END-EXEC
           EXEC CICS SEND TEXT
                FROM(WS-MAP-OUT)
                LENGTH(LENGTH OF WS-MAP-OUT)
                ERASE
           END-EXEC.
       8000-SEND-ERROR.
           EXEC CICS SEND TEXT
                FROM(MO-STATUS)
                LENGTH(10)
                ERASE
           END-EXEC
           EXEC CICS RETURN END-EXEC.

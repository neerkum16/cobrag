       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYSETL1.
       AUTHOR. SAMPLE.
      *----------------------------------------------------------------
      * NIGHTLY PAYMENT SETTLEMENT
      * READS TXN FILE, VALIDATES AGAINST VSAM ACCOUNT MASTER,
      * NETS ACTIVE HOLDS, POSTS TO DB2 PAYMENT_HIST, UPDATES
      * ACCOUNT_BAL AND THE KSDS LEDGER BALANCE.
      *----------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT TXN-FILE    ASSIGN TO TXNIN
                  ORGANIZATION IS SEQUENTIAL
                  FILE STATUS IS WS-TXN-FS.
           SELECT ACCT-MASTER ASSIGN TO ACCTMST
                  ORGANIZATION IS INDEXED
                  ACCESS MODE IS RANDOM
                  RECORD KEY IS ACCT-KEY
                  FILE STATUS IS WS-ACCT-FS.
           SELECT REJECT-FILE ASSIGN TO REJOUT
                  FILE STATUS IS WS-REJ-FS.
       DATA DIVISION.
       FILE SECTION.
       FD  TXN-FILE
           RECORDING MODE IS F.
           COPY TXNREC.
       FD  ACCT-MASTER.
           COPY ACCTREC.
       FD  REJECT-FILE
           RECORDING MODE IS F.
       01  REJECT-REC.
           05  REJ-TXN-ID              PIC X(16).
           05  REJ-REASON-CD           PIC X(04).
           05  REJ-REASON-TXT          PIC X(40).
           05  FILLER                  PIC X(20).
       WORKING-STORAGE SECTION.
           COPY PAYWS.
           EXEC SQL INCLUDE SQLCA END-EXEC.
           EXEC SQL
               DECLARE CSR_PENDING CURSOR WITH HOLD FOR
               SELECT HOLD_ID, HOLD_AMT
                 FROM PAYMENT_HOLD
                WHERE ACCT_NO = :WS-ACCT-NO
                  AND HOLD_STATUS = 'A'
           END-EXEC.
       PROCEDURE DIVISION.
       0000-MAINLINE.
           DISPLAY WS-BANNER
           PERFORM 1000-INITIALIZE THRU 1000-EXIT
           PERFORM 2000-PROCESS-TXN THRU 2000-EXIT
               UNTIL WS-EOF-TXN
           PERFORM 9000-TERMINATE
           GOBACK.
       1000-INITIALIZE.
           OPEN INPUT  TXN-FILE
                I-O    ACCT-MASTER
                OUTPUT REJECT-FILE
           IF WS-TXN-FS NOT = '00'
              MOVE 'OPEN TXNIN FAILED' TO WS-ABEND-MSG
              PERFORM 9999-ABEND
           END-IF
           CALL WS-DATE-PGM USING WS-DATE-PARMS
           MOVE WS-DP-CURR-DATE TO WS-RUN-DATE
           PERFORM 2900-READ-TXN.
       1000-EXIT.
           EXIT.
       2000-PROCESS-TXN.
           ADD 1 TO WS-TXN-READ-CNT
           PERFORM 2100-VALIDATE-TXN
           IF WS-TXN-VALID
              PERFORM 2200-APPLY-HOLDS
              PERFORM 2300-POST-PAYMENT
           ELSE
              PERFORM 2800-WRITE-REJECT
              GO TO 2000-READ-NEXT
           END-IF
           .
       2000-READ-NEXT.
           PERFORM 2900-READ-TXN.
       2000-EXIT.
           EXIT.
       2100-VALIDATE-TXN.
           SET WS-TXN-VALID TO TRUE
           MOVE SPACES TO WS-REJ-REASON
           IF TXN-AMOUNT NOT > ZERO
              SET WS-TXN-INVALID TO TRUE
              MOVE 'R001' TO WS-REJ-REASON
           END-IF
           EVALUATE TRUE
              WHEN TXN-TYPE-DEBIT
              WHEN TXN-TYPE-CREDIT
                 CONTINUE
              WHEN OTHER
                 SET WS-TXN-INVALID TO TRUE
                 MOVE 'R002' TO WS-REJ-REASON
           END-EVALUATE
           IF WS-TXN-VALID
              MOVE TXN-ACCT-NO TO ACCT-KEY
              READ ACCT-MASTER
                 INVALID KEY
                    SET WS-TXN-INVALID TO TRUE
                    MOVE 'R003' TO WS-REJ-REASON
              END-READ
           END-IF
           IF WS-TXN-VALID AND ACCT-STAT-CLOSED
              SET WS-TXN-INVALID TO TRUE
              MOVE 'R004' TO WS-REJ-REASON
           END-IF.
       2200-APPLY-HOLDS.
           MOVE TXN-ACCT-NO TO WS-ACCT-NO
           MOVE ZERO TO WS-HOLD-TOTAL
           EXEC SQL OPEN CSR_PENDING END-EXEC
           PERFORM UNTIL SQLCODE NOT = 0
              EXEC SQL
                 FETCH CSR_PENDING
                  INTO :WS-HOLD-ID, :WS-HOLD-AMT
              END-EXEC
              IF SQLCODE = 0
                 ADD WS-HOLD-AMT TO WS-HOLD-TOTAL
              END-IF
           END-PERFORM
           EXEC SQL CLOSE CSR_PENDING END-EXEC.
       2300-POST-PAYMENT.
           COMPUTE WS-NET-AMT ROUNDED =
                   TXN-AMOUNT - WS-HOLD-TOTAL
           MOVE TXN-ID        TO PH-TXN-ID
           MOVE TXN-ACCT-NO   TO PH-ACCT-NO
           MOVE WS-NET-AMT    TO PH-AMOUNT
           MOVE WS-RUN-DATE   TO PH-POST-DATE
           EXEC SQL
              INSERT INTO PAYMENT_HIST
                     (TXN_ID, ACCT_NO, AMOUNT, POST_DATE)
              VALUES (:PH-TXN-ID, :PH-ACCT-NO, :PH-AMOUNT,
                      :PH-POST-DATE)
           END-EXEC
           IF SQLCODE NOT = 0
              MOVE 'INSERT PAYMENT_HIST FAILED' TO WS-ABEND-MSG
              PERFORM 9999-ABEND
           END-IF
           EXEC SQL
              UPDATE ACCOUNT_BAL
                 SET CURR_BAL     = CURR_BAL + :WS-NET-AMT,
                     LAST_POST_DT = :WS-RUN-DATE
               WHERE ACCT_NO = :PH-ACCT-NO
           END-EXEC
           IF TXN-TYPE-DEBIT
              SUBTRACT WS-NET-AMT FROM ACCT-LEDGER-BAL
           ELSE
              ADD WS-NET-AMT TO ACCT-LEDGER-BAL
           END-IF
           ADD 1 TO ACCT-TXN-CNT
           REWRITE ACCT-RECORD
              INVALID KEY
                 MOVE 'REWRITE ACCTMST FAILED' TO WS-ABEND-MSG
                 PERFORM 9999-ABEND
           END-REWRITE
           ADD 1 TO WS-TXN-POSTED-CNT.
       2800-WRITE-REJECT.
           MOVE TXN-ID           TO REJ-TXN-ID
           MOVE WS-REJ-REASON    TO REJ-REASON-CD
           SEARCH ALL WS-REASON-ENTRY
              AT END
                 MOVE 'UNKNOWN REASON' TO REJ-REASON-TXT
              WHEN WS-REASON-CODE (WS-RSN-IDX) = WS-REJ-REASON
                 MOVE WS-REASON-TEXT (WS-RSN-IDX) TO REJ-REASON-TXT
           END-SEARCH
           WRITE REJECT-REC
           ADD 1 TO WS-TXN-REJ-CNT.
       2900-READ-TXN.
           READ TXN-FILE
              AT END
                 SET WS-EOF-TXN TO TRUE
           END-READ.
       3000-LEGACY-RECON.
      *    RETIRED 2019 - RECON MOVED TO DOWNSTREAM JOB PAYRCN02
           MOVE ZERO TO WS-HOLD-TOTAL
           EXEC SQL
              DELETE FROM PAYMENT_HOLD
               WHERE HOLD_STATUS = 'X'
           END-EXEC.
       9000-TERMINATE.
           EXEC SQL COMMIT END-EXEC
           CLOSE TXN-FILE ACCT-MASTER REJECT-FILE
           DISPLAY 'PAYSETL1 READ   : ' WS-TXN-READ-CNT
           DISPLAY 'PAYSETL1 POSTED : ' WS-TXN-POSTED-CNT
           DISPLAY 'PAYSETL1 REJECT : ' WS-TXN-REJ-CNT.
       9999-ABEND.
           DISPLAY 'PAYSETL1 ABEND: ' WS-ABEND-MSG
           EXEC SQL ROLLBACK END-EXEC
           MOVE 16 TO RETURN-CODE
           CALL 'CEE3ABD' USING WS-ABEND-CODE WS-ABEND-TIMING
           GOBACK.

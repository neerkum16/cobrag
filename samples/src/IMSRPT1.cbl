       IDENTIFICATION DIVISION.
       PROGRAM-ID. IMSRPT1.
      *----------------------------------------------------------------
      * IMS DL/I BATCH - CAP CUSTOMER RISK SCORE AT 700
      *----------------------------------------------------------------
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  DLI-FUNCTIONS.
           05  FUNC-GU                 PIC X(04) VALUE 'GU  '.
           05  FUNC-GN                 PIC X(04) VALUE 'GN  '.
           05  FUNC-REPL               PIC X(04) VALUE 'REPL'.
       01  CUST-SEG-IO.
           05  CUST-ID                 PIC X(10).
           05  CUST-NAME               PIC X(40).
           05  CUST-RISK-SCORE         PIC S9(03) COMP-3.
       01  CUST-SSA.
           05  FILLER                  PIC X(19)
                                       VALUE 'CUSTSEG (CUSTID  EQ'.
           05  SSA-CUST-ID             PIC X(10).
           05  FILLER                  PIC X(01) VALUE ')'.
       LINKAGE SECTION.
       01  PCB-MASK.
           05  PCB-DBD-NAME            PIC X(08).
           05  PCB-SEG-LEVEL           PIC X(02).
           05  PCB-STATUS              PIC X(02).
               88  PCB-OK                        VALUE SPACES.
               88  PCB-NOT-FOUND                 VALUE 'GE'.
               88  PCB-END-DB                    VALUE 'GB'.
           05  FILLER                  PIC X(20).
       PROCEDURE DIVISION USING PCB-MASK.
       0000-MAIN.
           MOVE 'C000000001' TO SSA-CUST-ID
           CALL 'CBLTDLI' USING FUNC-GU PCB-MASK CUST-SEG-IO CUST-SSA
           IF PCB-OK
              PERFORM 1000-RESCORE
           END-IF
           PERFORM UNTIL PCB-END-DB
              CALL 'CBLTDLI' USING FUNC-GN PCB-MASK CUST-SEG-IO
              IF PCB-OK
                 PERFORM 1000-RESCORE
              END-IF
           END-PERFORM
           GOBACK.
       1000-RESCORE.
           IF CUST-RISK-SCORE > 700
              MOVE 700 TO CUST-RISK-SCORE
              CALL 'CBLTDLI' USING FUNC-REPL PCB-MASK CUST-SEG-IO
           END-IF.

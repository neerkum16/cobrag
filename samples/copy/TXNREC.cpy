      *----------------------------------------------------------------
      * TXNREC - INBOUND PAYMENT TRANSACTION (LRECL 67, RECFM FB)
      *----------------------------------------------------------------
       01  TXN-RECORD.
           05  TXN-ID                  PIC X(16).
           05  TXN-ACCT-NO             PIC X(12).
           05  TXN-TYPE                PIC X(01).
               88  TXN-TYPE-DEBIT                VALUE 'D'.
               88  TXN-TYPE-CREDIT               VALUE 'C'.
           05  TXN-AMOUNT              PIC S9(11)V99 COMP-3.
           05  TXN-EFF-DATE.
               10  TXN-EFF-CCYY        PIC 9(04).
               10  TXN-EFF-MM          PIC 9(02).
               10  TXN-EFF-DD          PIC 9(02).
           05  TXN-EFF-DATE-N REDEFINES TXN-EFF-DATE
                                       PIC 9(08).
           05  TXN-CHANNEL             PIC X(03).
           05  FILLER                  PIC X(20).

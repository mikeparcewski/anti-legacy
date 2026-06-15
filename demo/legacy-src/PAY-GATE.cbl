       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAY-GATE.
      *---------------------------------------------------------------
      * PAY-GATE - Payment gateway interface.
      * Called by BILLING to process payments.
      * Validates payment, applies business rules, writes to ledger.
      *---------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT LEDGER-FILE ASSIGN TO 'PAYLEDGR'.
       DATA DIVISION.
       FILE SECTION.
       FD LEDGER-FILE.
       01 LEDGER-RECORD.
           05 LDGR-DATE        PIC 9(8).
           05 LDGR-CUST-ID     PIC 9(8).
           05 LDGR-AMOUNT      PIC S9(9)V99 COMP-3.
           05 LDGR-TYPE        PIC X(3).
              88 LDGR-PAYMENT  VALUE 'PAY'.
              88 LDGR-REFUND   VALUE 'REF'.
              88 LDGR-ADJUST   VALUE 'ADJ'.
           05 LDGR-STATUS      PIC X(1).
              88 LDGR-POSTED   VALUE 'P'.
              88 LDGR-PENDING  VALUE 'W'.
              88 LDGR-REJECTED VALUE 'R'.
       WORKING-STORAGE SECTION.
       01 WS-MAX-SINGLE-PAY   PIC S9(7)V99 VALUE 99999.99.
       01 WS-DAILY-LIMIT      PIC S9(9)V99 VALUE 500000.00.
       01 WS-DAILY-TOTAL       PIC S9(9)V99 VALUE 0.
       LINKAGE SECTION.
       01 LS-INVOICE.
           05 LS-INV-CUST-ID  PIC 9(8).
           05 LS-INV-AMOUNT   PIC S9(7)V99 COMP-3.
           05 LS-INV-TAX      PIC S9(5)V99 COMP-3.
           05 LS-INV-TOTAL    PIC S9(7)V99 COMP-3.
           05 LS-INV-DATE     PIC 9(8).
       01 LS-CUSTOMER.
           05 LS-CUST-ID      PIC 9(8).
           05 LS-CUST-NAME    PIC X(40).
           05 LS-CUST-ADDR    PIC X(60).
           05 LS-CUST-STATE   PIC X(2).
           05 LS-CUST-ZIP     PIC 9(5).
           05 LS-CUST-BALANCE PIC S9(9)V99 COMP-3.
           05 LS-CUST-STATUS  PIC X(1).
       PROCEDURE DIVISION USING LS-INVOICE LS-CUSTOMER.
       0000-MAIN.
           OPEN OUTPUT LEDGER-FILE
           PERFORM 1000-VALIDATE-PAYMENT
           CLOSE LEDGER-FILE
           GOBACK.
       1000-VALIDATE-PAYMENT.
      * Rule 1: Single payment cannot exceed $99,999.99
           IF LS-INV-TOTAL > WS-MAX-SINGLE-PAY
               SET LDGR-REJECTED TO TRUE
               PERFORM 3000-WRITE-LEDGER
               GOBACK
           END-IF
      * Rule 2: Daily aggregate cannot exceed $500,000.00
           ADD LS-INV-TOTAL TO WS-DAILY-TOTAL
           IF WS-DAILY-TOTAL > WS-DAILY-LIMIT
               SET LDGR-REJECTED TO TRUE
               PERFORM 3000-WRITE-LEDGER
               GOBACK
           END-IF
      * Rule 3: Customer must be active (status not 'C' or 'S')
           IF LS-CUST-STATUS NOT = 'A'
               SET LDGR-REJECTED TO TRUE
               PERFORM 3000-WRITE-LEDGER
               GOBACK
           END-IF
           PERFORM 2000-PROCESS-PAYMENT.
       2000-PROCESS-PAYMENT.
           MOVE LS-INV-DATE   TO LDGR-DATE
           MOVE LS-CUST-ID    TO LDGR-CUST-ID
           MOVE LS-INV-TOTAL  TO LDGR-AMOUNT
           SET LDGR-PAYMENT   TO TRUE
           SET LDGR-POSTED    TO TRUE
           PERFORM 3000-WRITE-LEDGER.
       3000-WRITE-LEDGER.
           WRITE LEDGER-RECORD.

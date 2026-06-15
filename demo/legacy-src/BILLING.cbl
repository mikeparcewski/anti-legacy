       IDENTIFICATION DIVISION.
       PROGRAM-ID. BILLING.
      *---------------------------------------------------------------
      * BILLING - Calculates invoice totals with tax.
      * Reads CUSTOMER file for state, looks up tax rate,
      * calls PAY-GATE for payment processing.
      *---------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT CUST-FILE ASSIGN TO 'CUSTMAST'
               ORGANIZATION IS INDEXED
               ACCESS MODE IS RANDOM
               RECORD KEY IS CUST-ID.
           SELECT INV-FILE ASSIGN TO 'INVOICES'.
       DATA DIVISION.
       FILE SECTION.
       FD CUST-FILE.
       01 CUST-RECORD.
           05 CUST-ID          PIC 9(8).
           05 CUST-NAME        PIC X(40).
           05 CUST-ADDR        PIC X(60).
           05 CUST-STATE       PIC X(2).
           05 CUST-ZIP         PIC 9(5).
           05 CUST-BALANCE     PIC S9(9)V99 COMP-3.
           05 CUST-STATUS      PIC X(1).
       FD INV-FILE.
       01 INV-RECORD.
           05 INV-CUST-ID      PIC 9(8).
           05 INV-AMOUNT       PIC S9(7)V99 COMP-3.
           05 INV-TAX          PIC S9(5)V99 COMP-3.
           05 INV-TOTAL        PIC S9(7)V99 COMP-3.
           05 INV-DATE         PIC 9(8).
       WORKING-STORAGE SECTION.
           COPY TAXRATES.
       01 WS-TAX-RATE          PIC V9(4) VALUE .0000.
       01 WS-SUBTOTAL          PIC S9(9)V99 VALUE 0.
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT CUST-FILE
           OPEN I-O INV-FILE
           PERFORM 1000-CALC-INVOICE
           CLOSE CUST-FILE
           CLOSE INV-FILE
           GOBACK.
       1000-CALC-INVOICE.
           READ CUST-FILE
               INVALID KEY
                   DISPLAY 'CUSTOMER NOT FOUND: ' INV-CUST-ID
                   GOBACK
           END-READ
           EXEC SQL
               SELECT TAX_RATE INTO :WS-TAX-RATE
               FROM TAX_CONFIG
               WHERE STATE_CODE = :CUST-STATE
           END-EXEC
           COMPUTE INV-TAX = INV-AMOUNT * WS-TAX-RATE
           COMPUTE INV-TOTAL = INV-AMOUNT + INV-TAX
           ADD INV-TOTAL TO CUST-BALANCE
           REWRITE CUST-RECORD
           WRITE INV-RECORD
           CALL 'PAY-GATE' USING INV-RECORD CUST-RECORD.

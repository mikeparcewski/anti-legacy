       IDENTIFICATION DIVISION.
       PROGRAM-ID. CUSTMGR.
      *---------------------------------------------------------------
      * CUSTOMER MANAGEMENT - Master customer lookup and maintenance.
      * Reads from CUSTOMER master file, writes to CUST-LOG.
      *---------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT CUST-FILE ASSIGN TO 'CUSTMAST'
               ORGANIZATION IS INDEXED
               ACCESS MODE IS DYNAMIC
               RECORD KEY IS CUST-ID
               FILE STATUS IS WS-FILE-STATUS.
           SELECT LOG-FILE ASSIGN TO 'CUSTLOG'.
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
              88 CUST-ACTIVE   VALUE 'A'.
              88 CUST-CLOSED   VALUE 'C'.
              88 CUST-SUSPENDED VALUE 'S'.
       FD LOG-FILE.
       01 LOG-RECORD           PIC X(120).
       WORKING-STORAGE SECTION.
       01 WS-FILE-STATUS       PIC X(2).
       01 WS-RETURN-CODE       PIC 9(4) VALUE 0.
       01 WS-TIMESTAMP         PIC X(26).
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN I-O CUST-FILE
           OPEN OUTPUT LOG-FILE
           PERFORM 1000-PROCESS-REQUEST
           CLOSE CUST-FILE
           CLOSE LOG-FILE
           GOBACK.
       1000-PROCESS-REQUEST.
           READ CUST-FILE
               INVALID KEY
                   MOVE 1001 TO WS-RETURN-CODE
                   PERFORM 9000-LOG-EVENT
               NOT INVALID KEY
                   IF CUST-ACTIVE
                       PERFORM 2000-UPDATE-CUSTOMER
                   ELSE
                       MOVE 1002 TO WS-RETURN-CODE
                       PERFORM 9000-LOG-EVENT
                   END-IF
           END-READ.
       2000-UPDATE-CUSTOMER.
           REWRITE CUST-RECORD
           MOVE 0 TO WS-RETURN-CODE
           PERFORM 9000-LOG-EVENT.
       9000-LOG-EVENT.
           MOVE FUNCTION CURRENT-DATE TO WS-TIMESTAMP
           STRING WS-TIMESTAMP DELIMITED SIZE
                  '|' DELIMITED SIZE
                  CUST-ID DELIMITED SIZE
                  '|' DELIMITED SIZE
                  WS-RETURN-CODE DELIMITED SIZE
                  INTO LOG-RECORD
           WRITE LOG-RECORD.

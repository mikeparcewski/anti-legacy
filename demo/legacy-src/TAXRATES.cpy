       IDENTIFICATION DIVISION.
       PROGRAM-ID. TAXRATES.
      *---------------------------------------------------------------
      * TAXRATES - Copybook for tax rate table lookups.
      *---------------------------------------------------------------
       01 WS-TAX-TABLE.
           05 WS-TAX-ENTRY OCCURS 50 TIMES.
               10 WS-TAX-STATE    PIC X(2).
               10 WS-TAX-RATE-TBL PIC V9(4).

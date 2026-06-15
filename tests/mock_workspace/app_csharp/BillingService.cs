using System;
namespace LegacyApp.CSharp
{
    public class BillingService
    {
        public DbSet<TaxConfig> Taxes { get; set; }
    }
}

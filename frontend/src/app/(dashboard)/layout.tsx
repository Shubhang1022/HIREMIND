import { AppSidebar } from '@/components/layout/Sidebar';

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-full flex">
      <AppSidebar />
      <main className="flex-1 ml-0 lg:ml-64 flex flex-col pt-16 lg:pt-[73px]">
        {children}
      </main>
    </div>
  );
}

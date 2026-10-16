import { redirect } from "next/navigation";
import { getViewer } from "@/lib/auth";

/** Send each user to the right place: admins to the client list, a client
 *  user with one client straight to its dashboard. */
export default async function Home() {
  const viewer = await getViewer();
  if (viewer.role === "admin") redirect("/clients");
  if (viewer.clients.length === 1) redirect(`/c/${viewer.clients[0].id}`);
  if (viewer.clients.length > 1) redirect("/clients");
  redirect("/no-access");
}

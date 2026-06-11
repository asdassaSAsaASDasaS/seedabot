import sqlite3
import flet as ft
import webbrowser
import threading
import time

def main_dashboard(page: ft.Page):
    print("DEBUG: main_dashboard() start")
    page.title = "The Green Oasis - Advanced Admin Panel"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 25
    page.scroll = ft.ScrollMode.AUTO
    # debug banner to verify the page is being populated
    page.add(ft.Text("DEBUG: UI loaded", color=ft.Colors.GREEN_400, weight=ft.FontWeight.BOLD))
    page.update()

    # --- INVENTORY STATE ---
    CATALOG = {
        "Bangalore": [
            {"name": "Monstera Deliciosa", "price": 1200},
            {"name": "Snake Plant (Laurentii)", "price": 450},
        ],
        "Mangalore": [
            {"name": "Fiddle Leaf Fig", "price": 1500},
            {"name": "Peace Lily", "price": 350},
        ]
    }

    # --- DATA & LIST CONTAINERS ---
    stat_total = ft.Text("0", style=ft.TextThemeStyle.HEADLINE_MEDIUM, weight=ft.FontWeight.BOLD)
    stat_delivered = ft.Text("0", style=ft.TextThemeStyle.HEADLINE_MEDIUM, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_400)
    stat_pending = ft.Text("0", style=ft.TextThemeStyle.HEADLINE_MEDIUM, weight=ft.FontWeight.BOLD, color=ft.Colors.AMBER_400)

    orders_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID")),
            ft.DataColumn(ft.Text("Customer")),
            ft.DataColumn(ft.Text("City")),
            ft.DataColumn(ft.Text("Product")),
            ft.DataColumn(ft.Text("UTR Number")),
            ft.DataColumn(ft.Text("Status")),
        ],
        rows=[]
    )
    catalog_column = ft.Column(spacing=10)

    # --- EDIT OVERLAY ELEMENTS ---
    current_city, current_idx = "", None
    modal_title = ft.Text("Edit Product Details")
    field_name = ft.TextField(label="Product Name")
    field_price = ft.TextField(label="Price (₹)", keyboard_type=ft.KeyboardType.NUMBER)

    def close_modal(e):
        import sqlite3
        import flet as ft


        SAMPLE_CATALOG = {
            "Bangalore": [
                {"name": "Monstera Deliciosa", "price": 1200},
                {"name": "Snake Plant (Laurentii)", "price": 450},
            ],
            "Mangalore": [
                {"name": "Fiddle Leaf Fig", "price": 1500},
                {"name": "Peace Lily", "price": 350},
            ],
        }


        def main_dashboard(page: ft.Page):
            print("DEBUG: main_dashboard start")
            import sqlite3
            import flet as ft
            import webbrowser
            import threading
            import time


            SAMPLE_CATALOG = {
                "Bangalore": [
                    {"name": "Monstera Deliciosa", "price": 1200},
                    {"name": "Snake Plant (Laurentii)", "price": 450},
                ],
                "Mangalore": [
                    {"name": "Fiddle Leaf Fig", "price": 1500},
                    {"name": "Peace Lily", "price": 350},
                ],
            }


            def main_dashboard(page: ft.Page):
                print("DEBUG: main_dashboard start")
                page.title = "The Green Oasis - Admin"
                page.theme_mode = ft.ThemeMode.DARK
                page.padding = 20
                page.scroll = ft.ScrollMode.AUTO

                # --- simple visible debug text ---
                debug_text = ft.Text("DEBUG: UI loaded", color=ft.Colors.GREEN_400, weight=ft.FontWeight.BOLD)

                # --- stats ---
                total_txt = ft.Text("0", size=24, weight=ft.FontWeight.BOLD)
                delivered_txt = ft.Text("0", size=24, color=ft.Colors.GREEN_400, weight=ft.FontWeight.BOLD)
                pending_txt = ft.Text("0", size=24, color=ft.Colors.AMBER_400, weight=ft.FontWeight.BOLD)

                def stat_card(label, widget):
                    return ft.Container(
                        content=ft.Column([ft.Text(label, size=12), widget]),
                        padding=12,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                        border_radius=8,
                        expand=True,
                    )

                # --- orders table ---
                orders_table = ft.DataTable(
                    columns=[
                        ft.DataColumn(ft.Text("ID")),
                        ft.DataColumn(ft.Text("Customer")),
                        ft.DataColumn(ft.Text("City")),
                        ft.DataColumn(ft.Text("Product")),
                        ft.DataColumn(ft.Text("UTR")),
                        ft.DataColumn(ft.Text("Status")),
                    ],
                    rows=[],
                    expand=True,
                )

                # --- catalog list ---
                catalog_column = ft.Column(spacing=8)

                # modal editing
                edit_name = ft.TextField(label="Product Name")
                edit_price = ft.TextField(label="Price", keyboard_type=ft.KeyboardType.NUMBER)
                edit_dialog = ft.AlertDialog(title=ft.Text("Edit Product"), content=ft.Column([edit_name, edit_price]))
                page.overlay.append(edit_dialog)

                current_city = None
                current_idx = None

                def open_edit(city, idx):
                    nonlocal current_city, current_idx
                    current_city = city
                    current_idx = idx
                    prod = catalog.get(city, [])[idx]
                    edit_name.value = prod["name"]
                    edit_price.value = str(prod["price"])
                    edit_dialog.open = True
                    page.update()

                def save_edit(e):
                    nonlocal current_city, current_idx
                    try:
                        catalog[current_city][current_idx]["name"] = edit_name.value.strip()
                        catalog[current_city][current_idx]["price"] = int(edit_price.value.strip())
                    except Exception as err:
                        print("DEBUG: save_edit error:", err)
                    edit_dialog.open = False
                    refresh()

                edit_dialog.actions = [ft.TextButton("Cancel", on_click=lambda e: setattr(edit_dialog, 'open', False)), ft.Button("Save", on_click=save_edit, bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE)]

                # fallback in-memory catalog if DB not available
                catalog = {k: [dict(p) for p in v] for k, v in SAMPLE_CATALOG.items()}

                def load_orders_from_db():
                    try:
                        conn = sqlite3.connect("store.db")
                        cur = conn.cursor()
                        cur.execute("SELECT id, username, city, product, utr_no, status FROM orders ORDER BY id DESC")
                        rows = cur.fetchall()
                        conn.close()
                        return rows
                    except Exception as err:
                        print("DEBUG: DB load error:", err)
                        # return some sample orders to ensure UI shows content
                        return [
                            (1, "alice", "Bangalore", "Monstera Deliciosa", "UTR123", "DELIVERED"),
                            (2, "bob", "Mangalore", "Peace Lily", "UTR456", "PENDING"),
                        ]

                def refresh(e=None):
                    orders_table.rows.clear()
                    catalog_column.controls.clear()

                    records = load_orders_from_db()
                    total, dcount, pcount = 0, 0, 0
                    for r in records:
                        total += 1
                        status = r[5]
                        if status == "DELIVERED":
                            dcount += 1
                            color = ft.Colors.GREEN_400
                        else:
                            pcount += 1
                            color = ft.Colors.AMBER_400
                        orders_table.rows.append(ft.DataRow(cells=[
                            ft.DataCell(ft.Text(str(r[0]))),
                            ft.DataCell(ft.Text(f"@{r[1]}")),
                            ft.DataCell(ft.Text(r[2])),
                            ft.DataCell(ft.Text(r[3])),
                            ft.DataCell(ft.Text(r[4], color=ft.Colors.BLUE_200)),
                            ft.DataCell(ft.Text(status, color=color, weight=ft.FontWeight.BOLD)),
                        ]))

                    total_txt.value = str(total)
                    delivered_txt.value = str(dcount)
                    pending_txt.value = str(pcount)

                    for city, items in catalog.items():
                        catalog_column.controls.append(ft.Text(f"📍 {city}", weight=ft.FontWeight.BOLD))
                        for i, p in enumerate(items):
                            catalog_column.controls.append(
                                ft.Container(
                                    content=ft.Row([
                                        ft.Icon("eco", color=ft.Colors.GREEN_400),
                                        ft.Column([ft.Text(p["name"], weight=ft.FontWeight.BOLD), ft.Text(f"₹{p['price']}")], expand=True),
                                        ft.IconButton("edit", on_click=lambda e, c=city, idx=i: open_edit(c, idx)),
                                    ]),
                                    padding=8,
                                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                                    border_radius=6,
                                )
                            )

                    page.update()

                # --- layout ---
                header = ft.Row([ft.Text("🌿 The Green Oasis", style=ft.TextThemeStyle.HEADLINE_SMALL, color=ft.Colors.GREEN_400), ft.Button("Refresh", on_click=refresh)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

                stats = ft.Row([stat_card("Total", total_txt), stat_card("Delivered", delivered_txt), stat_card("Pending", pending_txt)], spacing=12)

                body = ft.Row([
                    ft.Container(content=ft.Column([ft.Text("Orders"), orders_table]), expand=2, padding=12, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, border_radius=8),
                    ft.Container(content=ft.Column([ft.Text("Catalog"), catalog_column]), expand=1, padding=12, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, border_radius=8),
                ], spacing=12, expand=True)

                main_col = ft.Column([debug_text, header, stats, body], spacing=16, expand=True)
                page.add(main_col)

                # initial populate
                refresh()


            if __name__ == "__main__":
                host = "127.0.0.1"
                port = 8550
                url = f"http://{host}:{port}"

                def _open_browser_delayed():
                    time.sleep(1)
                    try:
                        print(f"DEBUG: attempting to open browser at {url}")
                        webbrowser.open(url)
                    except Exception as e:
                        print("DEBUG: webbrowser open failed:", e)

                threading.Thread(target=_open_browser_delayed, daemon=True).start()

                print(f"DEBUG: attempting to start flet on {host}:{port}")
                try:
                    # try exporting ASGI app and run with uvicorn (more explicit server)
                    asgi_app = ft.run(main_dashboard, export_asgi_app=True)
                    if asgi_app is not None:
                        print("DEBUG: got ASGI app from flet, attempting to start uvicorn")
                        try:
                            import uvicorn
                            uvicorn.run(asgi_app, host=host, port=port)
                        except Exception as e:
                            print("DEBUG: uvicorn start failed:", e)
                            print("Falling back to ft.run()")
                            ft.run(main_dashboard, host=host, port=port, view=ft.controls.types.AppView.WEB_BROWSER)
                    else:
                        # ft.run returned None (e.g., on pyodide), fall back
                        ft.run(main_dashboard, host=host, port=port, view=ft.controls.types.AppView.WEB_BROWSER)
                except Exception as e:
                    print("DEBUG: ft.run(export_asgi_app=True) failed:", e)
                    print("Falling back to standard ft.run()")
                    ft.run(main_dashboard, host=host, port=port, view=ft.controls.types.AppView.WEB_BROWSER)

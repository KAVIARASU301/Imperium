from PySide6.QtWidgets import QMessageBox


def show_about(parent):
    """Displays a polished About dialog compatible with dark themes."""

    about_text = """
    <div style="
        font-family:'Segoe UI',sans-serif;
        font-size:11pt;
        line-height:1.6;
        color:#E6EAF2;
    ">

        <h2 style="margin-bottom:4px; color:#FFFFFF;">
            🦡 Options Badger Trading Terminal
        </h2>

        <div style="color:#9AA4B2; margin-bottom:12px;">
            Precision tools for serious options traders
        </div>

        <table style="margin-top:6px; margin-bottom:12px; color:#E6EAF2;">
            <tr>
                <td style="padding-right:14px; color:#A9B1C3;"><b>Version</b></td>
                <td>1.0.0</td>
            </tr>
            <tr>
                <td style="padding-right:14px; color:#A9B1C3;"><b>Author</b></td>
                <td>Kaviarasu Murugan</td>
            </tr>
            <tr>
                <td style="padding-right:14px; color:#A9B1C3;"><b>Contact</b></td>
                <td>kaviarasu301@gmail.com</td>
            </tr>
            <tr>
                <td style="padding-right:14px; color:#A9B1C3;"><b>©</b></td>
                <td>2025</td>
            </tr>
        </table>

        <hr style="margin:14px 0; border:1px solid #2A3140;">

        <p>
            <b>Options Badger</b> is a high-performance desktop trading terminal
            designed for speed, stability, and clarity during live market conditions.
        </p>

        <p>
            The platform is optimized for <b>options scalping, intraday monitoring,
            and disciplined risk management</b>, with tools built for fast decision-making.
        </p>

        <p>
            Built entirely in <b>Python</b> and powered by the
            <b>Kite Connect API</b>, the application supports both
            <b>Live Trading</b> and <b>Paper Trading</b> modes.
        </p>

        <div style="
            margin-top:18px;
            padding:14px;
            background:linear-gradient(180deg, #1C2232, #161A25);
            border-left:4px solid #F39C12;
            border-radius:8px;
        ">
            <div style="
                font-size:11.5pt;
                font-weight:700;
                letter-spacing:0.4px;
                color:#FFD37A;
                margin-bottom:6px;
            ">
                LICENSE NOTICE
            </div>

            <div style="font-size:10.8pt; color:#C7CEDB;">
                This software is intended for <b>personal use only</b>.<br>
                Sale, redistribution, reverse engineering, or commercial use
                without explicit written permission is strictly prohibited.
            </div>
        </div>
    </div>
    """

    QMessageBox.about(
        parent,
        "About Options Badger",
        about_text
    )

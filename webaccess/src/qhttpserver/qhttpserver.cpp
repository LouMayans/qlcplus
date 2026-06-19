/*
 * Copyright 2011-2014 Nikhil Marathe <nsm.nikhil@gmail.com>
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to
 * deal in the Software without restriction, including without limitation the
 * rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
 * sell copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
 * IN THE SOFTWARE.
 */

#include "qhttpserver.h"

#include <QTcpSocket>
#include <QSslSocket>
#include <QSslCertificate>
#include <QSslKey>
#include <QVariant>
#include <QFile>
#include <QFileInfo>
#include <QFileSystemWatcher>
#include <QTimer>
#include <QDebug>

#include "qhttpconnection.h"

QHash<int, QString> STATUS_CODES;

QHttpServer::QHttpServer(QObject *parent) : QObject(parent), m_tcpServer(0)
{
#define STATUS_CODE(num, reason) STATUS_CODES.insert(num, reason);
    // {{{
    STATUS_CODE(100, "Continue")
    STATUS_CODE(101, "Switching Protocols")
    STATUS_CODE(102, "Processing") // RFC 2518) obsoleted by RFC 4918
    STATUS_CODE(200, "OK")
    STATUS_CODE(201, "Created")
    STATUS_CODE(202, "Accepted")
    STATUS_CODE(203, "Non-Authoritative Information")
    STATUS_CODE(204, "No Content")
    STATUS_CODE(205, "Reset Content")
    STATUS_CODE(206, "Partial Content")
    STATUS_CODE(207, "Multi-Status") // RFC 4918
    STATUS_CODE(300, "Multiple Choices")
    STATUS_CODE(301, "Moved Permanently")
    STATUS_CODE(302, "Moved Temporarily")
    STATUS_CODE(303, "See Other")
    STATUS_CODE(304, "Not Modified")
    STATUS_CODE(305, "Use Proxy")
    STATUS_CODE(307, "Temporary Redirect")
    STATUS_CODE(400, "Bad Request")
    STATUS_CODE(401, "Unauthorized")
    STATUS_CODE(402, "Payment Required")
    STATUS_CODE(403, "Forbidden")
    STATUS_CODE(404, "Not Found")
    STATUS_CODE(405, "Method Not Allowed")
    STATUS_CODE(406, "Not Acceptable")
    STATUS_CODE(407, "Proxy Authentication Required")
    STATUS_CODE(408, "Request Time-out")
    STATUS_CODE(409, "Conflict")
    STATUS_CODE(410, "Gone")
    STATUS_CODE(411, "Length Required")
    STATUS_CODE(412, "Precondition Failed")
    STATUS_CODE(413, "Request Entity Too Large")
    STATUS_CODE(414, "Request-URI Too Large")
    STATUS_CODE(415, "Unsupported Media Type")
    STATUS_CODE(416, "Requested Range Not Satisfiable")
    STATUS_CODE(417, "Expectation Failed")
    STATUS_CODE(418, "I\"m a teapot")        // RFC 2324
    STATUS_CODE(422, "Unprocessable Entity") // RFC 4918
    STATUS_CODE(423, "Locked")               // RFC 4918
    STATUS_CODE(424, "Failed Dependency")    // RFC 4918
    STATUS_CODE(425, "Unordered Collection") // RFC 4918
    STATUS_CODE(426, "Upgrade Required")     // RFC 2817
    STATUS_CODE(500, "Internal Server Error")
    STATUS_CODE(501, "Not Implemented")
    STATUS_CODE(502, "Bad Gateway")
    STATUS_CODE(503, "Service Unavailable")
    STATUS_CODE(504, "Gateway Time-out")
    STATUS_CODE(505, "HTTP Version not supported")
    STATUS_CODE(506, "Variant Also Negotiates") // RFC 2295
    STATUS_CODE(507, "Insufficient Storage")    // RFC 4918
    STATUS_CODE(509, "Bandwidth Limit Exceeded")
    STATUS_CODE(510, "Not Extended") // RFC 2774
    // }}}
}

QHttpServer::~QHttpServer()
{
}

bool QHttpServer::listen(const QHostAddress &address, quint16 port)
{
    Q_ASSERT(!m_tcpServer);
    m_tcpServer = new CustomTcpServer(this);

    connect(m_tcpServer, SIGNAL(newRequest(QHttpRequest *, QHttpResponse *)), this,
            SIGNAL(newRequest(QHttpRequest *, QHttpResponse *)));
    connect(m_tcpServer, SIGNAL(webSocketDataReady(QHttpConnection*,QString)),
            this, SIGNAL(webSocketDataReady(QHttpConnection*,QString)));
    connect(m_tcpServer, SIGNAL(webSocketConnectionClose(QHttpConnection*)),
            this, SIGNAL(webSocketConnectionClose(QHttpConnection*)));

    if (m_useSsl)
        static_cast<CustomTcpServer *>(m_tcpServer)->setSslConfiguration(m_sslConfiguration);

    if (!m_tcpServer->listen(address, port))
    {
        delete m_tcpServer;
        m_tcpServer = NULL;
        return false;
    }
    return true;
}

bool QHttpServer::listen(quint16 port)
{
    return listen(QHostAddress::Any, port);
}

void QHttpServer::close()
{
    if (m_tcpServer)
        m_tcpServer->close();
}

bool QHttpServer::loadSslConfiguration(QSslConfiguration &cfg) const
{
    QList<QSslCertificate> chain = QSslCertificate::fromPath(m_certPath, QSsl::Pem);
    if (chain.isEmpty())
    {
        qWarning() << "[QHttpServer] No valid certificate found in" << m_certPath;
        return false;
    }

    QFile keyFile(m_keyPath);
    if (!keyFile.open(QIODevice::ReadOnly))
    {
        qWarning() << "[QHttpServer] Unable to open private key file" << m_keyPath;
        return false;
    }
    QByteArray keyData = keyFile.readAll();
    keyFile.close();

    // Try the common PEM private key algorithms in turn (RSA, EC, DSA).
    // This covers traditional ("BEGIN RSA PRIVATE KEY") and EC keys.
    QSslKey privateKey;
    const QSsl::KeyAlgorithm algorithms[] = { QSsl::Rsa, QSsl::Ec, QSsl::Dsa };
    for (QSsl::KeyAlgorithm algo : algorithms)
    {
        privateKey = QSslKey(keyData, algo, QSsl::Pem);
        if (!privateKey.isNull())
            break;
    }
    if (privateKey.isNull())
    {
        qWarning() << "[QHttpServer] Invalid or unsupported private key in" << m_keyPath
                   << "(expected an unencrypted PEM RSA/EC key)";
        return false;
    }

    cfg.setLocalCertificateChain(chain);
    cfg.setPrivateKey(privateKey);
    return true;
}

bool QHttpServer::setSslConfiguration(const QString &certPath, const QString &keyPath)
{
    m_certPath = certPath;
    m_keyPath = keyPath;

    QSslConfiguration cfg = QSslConfiguration::defaultConfiguration();
    if (!loadSslConfiguration(cfg))
        return false;

    m_sslConfiguration = cfg;
    m_useSsl = true;
    qDebug() << "[QHttpServer] TLS enabled using certificate" << certPath;

    // Watch the cert/key files so a renewed certificate is picked up live,
    // without restarting the server.
    setupCertWatcher();
    return true;
}

void QHttpServer::setupCertWatcher()
{
    if (m_certWatcher == nullptr)
    {
        m_certWatcher = new QFileSystemWatcher(this);
        m_reloadTimer = new QTimer(this);
        m_reloadTimer->setSingleShot(true);
        m_reloadTimer->setInterval(2000); // coalesce events; let cert+key both settle

        connect(m_reloadTimer, &QTimer::timeout, this, [this]() { reloadCertificate(); });
        // A cert tool typically writes cert and key separately and may replace
        // (delete+create) the files, so react to both file and directory events.
        connect(m_certWatcher, &QFileSystemWatcher::fileChanged, this,
                [this](const QString &) { m_reloadRetries = 0; m_reloadTimer->start(); });
        connect(m_certWatcher, &QFileSystemWatcher::directoryChanged, this,
                [this](const QString &) { m_reloadRetries = 0; m_reloadTimer->start(); });
    }
    watchCertPaths();
}

void QHttpServer::watchCertPaths()
{
    if (m_certWatcher == nullptr)
        return;

    if (!m_certWatcher->files().isEmpty())
        m_certWatcher->removePaths(m_certWatcher->files());
    if (!m_certWatcher->directories().isEmpty())
        m_certWatcher->removePaths(m_certWatcher->directories());

    // Watch the files themselves and their containing folder (so a file that is
    // replaced rather than modified in place is still detected).
    for (const QString &p : { m_certPath, m_keyPath })
    {
        if (QFileInfo::exists(p))
            m_certWatcher->addPath(p);
        const QString dir = QFileInfo(p).absolutePath();
        if (!dir.isEmpty() && !m_certWatcher->directories().contains(dir))
            m_certWatcher->addPath(dir);
    }
}

void QHttpServer::reloadCertificate()
{
    QSslConfiguration cfg = QSslConfiguration::defaultConfiguration();
    if (loadSslConfiguration(cfg))
    {
        m_reloadRetries = 0;
        m_sslConfiguration = cfg;
        if (m_tcpServer != nullptr)
            static_cast<CustomTcpServer *>(m_tcpServer)->setSslConfiguration(cfg);
        qDebug() << "[QHttpServer] TLS certificate reloaded from disk";
    }
    else if (++m_reloadRetries <= 5)
    {
        // Likely a partial write (only one of cert/key updated yet) — keep the
        // current cert and try again shortly (bounded retries).
        qWarning() << "[QHttpServer] Certificate reload failed (attempt" << m_reloadRetries
                   << "); keeping previous certificate";
        m_reloadTimer->start();
    }
    else
    {
        qWarning() << "[QHttpServer] Giving up reloading certificate after repeated failures;"
                   << "keeping previous certificate";
    }
    // Re-arm watches in case the files were replaced (which drops the watch).
    watchCertPaths();
}

CustomTcpServer::CustomTcpServer(QObject *parent)
    : QTcpServer(parent)
{
}

void CustomTcpServer::setSslConfiguration(const QSslConfiguration &configuration)
{
    m_sslConfiguration = configuration;
    m_useSsl = true;
}

void CustomTcpServer::incomingConnection(qintptr handle)
{
    QTcpSocket *socket;

    if (m_useSsl)
    {
        QSslSocket *sslSocket = new QSslSocket(this);
        sslSocket->setSslConfiguration(m_sslConfiguration);
        // We are a public server: never ask the browser for a client
        // certificate (the default QueryPeer mode makes browsers prompt the
        // user to choose a client cert).
        sslSocket->setPeerVerifyMode(QSslSocket::VerifyNone);
        if (!sslSocket->setSocketDescriptor(handle))
        {
            qWarning() << "[QHttpServer] Unable to set SSL socket descriptor";
            delete sslSocket;
            return;
        }
        // Begin the TLS handshake. The decrypted HTTP/WS data only reaches the
        // parser once the handshake completes, so downstream code is unchanged.
        sslSocket->startServerEncryption();
        socket = sslSocket;
    }
    else
    {
        socket = new QTcpSocket(this);
        socket->setSocketDescriptor(handle);
    }

    QHttpConnection *connection = new QHttpConnection(socket, this);
    connect(connection, SIGNAL(newRequest(QHttpRequest*, QHttpResponse*)),
            this, SIGNAL(newRequest(QHttpRequest*, QHttpResponse*)));
    connect(connection, SIGNAL(webSocketDataReady(QHttpConnection*,QString)),
            this, SIGNAL(webSocketDataReady(QHttpConnection*,QString)));
    connect(connection, SIGNAL(webSocketConnectionClose(QHttpConnection*)),
            this, SIGNAL(webSocketConnectionClose(QHttpConnection*)));
}

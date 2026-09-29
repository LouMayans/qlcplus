/*
  Q Light Controller Plus
  webaccessstage.cpp

  Copyright (c) Massimo Callegari

  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0.txt

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
*/

#include <QJsonParseError>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QFileInfo>
#include <QSaveFile>
#include <QVector3D>
#include <QMetaType>
#include <QVariant>
#include <QDebug>
#include <QColor>
#include <QTimer>
#include <QFile>
#include <QDir>

#include "webaccessstage.h"
#include "qhttpconnection.h"
#include "monitorproperties.h"
#include "inputoutputmap.h"
#include "qlcfixturemode.h"
#include "qlcfixturehead.h"
#include "qlcfixturedef.h"
#include "qlccapability.h"
#include "qlcphysical.h"
#include "qlcchannel.h"
#include "qlcconfig.h"
#include "universe.h"
#include "qlcfile.h"
#include "fixture.h"
#include "doc.h"

#define DMX_TICK_MS         33
#define RIG_DEBOUNCE_MS     300
#define MAX_JSON_FILE_SIZE  (16 * 1024 * 1024)

WebAccessStage::WebAccessStage(Doc *doc, QObject *parent)
    : QObject(parent)
    , m_doc(doc)
    , m_dmxTimer(new QTimer(this))
    , m_rigTimer(new QTimer(this))
    , m_serial(1)
    , m_stageRev(0)
    , m_propsRev(0)
{
    m_dmxTimer->setInterval(DMX_TICK_MS);
    connect(m_dmxTimer, SIGNAL(timeout()), this, SLOT(slotDmxTick()));

    m_rigTimer->setSingleShot(true);
    m_rigTimer->setInterval(RIG_DEBOUNCE_MS);
    connect(m_rigTimer, SIGNAL(timeout()), this, SLOT(slotRigTimeout()));

    connect(m_doc, SIGNAL(fixtureAdded(quint32)), this, SLOT(slotRigChanged()));
    connect(m_doc, SIGNAL(fixtureRemoved(quint32)), this, SLOT(slotRigChanged()));
    connect(m_doc, SIGNAL(fixtureChanged(quint32)), this, SLOT(slotRigChanged()));
    connect(m_doc, SIGNAL(loaded()), this, SLOT(slotRigChanged()));
}

/*********************************************************************
 * Rig
 *********************************************************************/

static QJsonValue resourceToJson(const QVariant &res)
{
    if (!res.isValid())
        return QJsonValue();

    switch (res.userType())
    {
        case QMetaType::QColor:
            return res.value<QColor>().name();
        case QMetaType::QString:
        {
            // gobo images are sent relative to the Gobos folder, served at /gobos/
            QString path = res.toString();
            QDir goboDir = QLCFile::systemDirectory(GOBODIR);
            if (QFileInfo(path).isAbsolute())
                path = goboDir.relativeFilePath(path);
            return path;
        }
        case QMetaType::Int:
        case QMetaType::UInt:
        case QMetaType::Double:
        case QMetaType::Float:
            return res.toDouble();
        default:
            return QJsonValue();
    }
}

static QJsonObject physicalToJson(const QLCPhysical &phy)
{
    QJsonObject obj;
    obj["panMax"] = phy.focusPanMax();
    obj["tiltMax"] = phy.focusTiltMax();
    obj["lensMin"] = phy.lensDegreesMin();
    obj["lensMax"] = phy.lensDegreesMax();
    obj["width"] = phy.width();
    obj["height"] = phy.height();
    obj["depth"] = phy.depth();
    obj["focusType"] = phy.focusType();
    QJsonArray layout;
    layout.append(phy.layoutSize().width());
    layout.append(phy.layoutSize().height());
    obj["layout"] = layout;
    return obj;
}

QString WebAccessStage::rigJson(const QString &showPath) const
{
    QJsonObject root;
    root["serial"] = qint64(m_serial);
    root["show"] = showPath;

    QJsonArray universes;
    foreach (Universe *uni, m_doc->inputOutputMap()->universes())
    {
        QJsonObject u;
        u["id"] = qint64(uni->id());
        u["name"] = uni->name();
        universes.append(u);
    }
    root["universes"] = universes;

    MonitorProperties *mProps = m_doc->monitorProperties();
    QJsonObject monitor;
    QJsonArray grid;
    grid.append(double(mProps->gridSize().x()));
    grid.append(double(mProps->gridSize().y()));
    grid.append(double(mProps->gridSize().z()));
    monitor["grid"] = grid;
    monitor["units"] = mProps->gridUnits() == MonitorProperties::Feet ? "ft" : "m";
    root["monitor"] = monitor;

    QJsonArray fixtures;
    foreach (Fixture *fxi, m_doc->fixtures())
    {
        QJsonObject fx;
        QLCFixtureDef *def = fxi->fixtureDef();
        QLCFixtureMode *mode = fxi->fixtureMode();

        fx["id"] = qint64(fxi->id());
        fx["name"] = fxi->name();
        fx["manufacturer"] = def ? def->manufacturer() : QString("Generic");
        fx["model"] = def ? def->model() : QString("Generic");
        fx["mode"] = mode ? mode->name() : QString();
        fx["type"] = fxi->typeString();
        fx["universe"] = qint64(fxi->universe());
        fx["address"] = qint64(fxi->address());
        fx["channels"] = qint64(fxi->channels());

        QLCPhysical phy;
        if (mode != NULL)
            phy = mode->physical();
        else if (def != NULL)
            phy = def->physical();
        fx["physical"] = physicalToJson(phy);

        QJsonArray heads;
        for (int h = 0; h < fxi->heads(); h++)
        {
            QJsonArray headChannels;
            foreach (quint32 ch, fxi->head(h).channels())
                headChannels.append(qint64(ch));
            heads.append(headChannels);
        }
        fx["heads"] = heads;

        QJsonArray channels;
        for (quint32 c = 0; c < fxi->channels(); c++)
        {
            const QLCChannel *channel = fxi->channel(c);
            QJsonObject ch;
            if (channel == NULL)
            {
                channels.append(ch);
                continue;
            }
            ch["name"] = channel->name();
            ch["group"] = QLCChannel::groupToString(channel->group());
            ch["byte"] = int(channel->controlByte());
            ch["colour"] = int(channel->colour());
            ch["preset"] = QLCChannel::presetToString(channel->preset());

            QJsonArray caps;
            foreach (QLCCapability *cap, channel->capabilities())
            {
                QJsonObject c;
                c["min"] = int(cap->min());
                c["max"] = int(cap->max());
                c["name"] = cap->name();
                c["preset"] = QLCCapability::presetToString(cap->preset());
                QJsonArray res;
                for (int r = 0; r < 2; r++)
                {
                    QJsonValue v = resourceToJson(cap->resource(r));
                    if (!v.isNull())
                        res.append(v);
                }
                if (!res.isEmpty())
                    c["res"] = res;
                caps.append(c);
            }
            ch["caps"] = caps;
            channels.append(ch);
        }
        fx["ch"] = channels;

        if (mProps->containsFixture(fxi->id()))
        {
            QVector3D pos = mProps->fixturePosition(fxi->id(), 0, 0);
            QJsonObject mon;
            mon["x"] = double(pos.x());
            mon["y"] = double(pos.y());
            mon["rot"] = double(mProps->fixtureRotation(fxi->id(), 0, 0).y());
            QColor gel = mProps->fixtureGelColor(fxi->id(), 0, 0);
            if (gel.isValid())
                mon["gel"] = gel.name();
            fx["monitor"] = mon;
        }
        else
        {
            fx["monitor"] = QJsonValue();
        }

        fixtures.append(fx);
    }
    root["fixtures"] = fixtures;

    return QString::fromUtf8(QJsonDocument(root).toJson(QJsonDocument::Compact));
}

void WebAccessStage::slotRigChanged()
{
    m_rigTimer->start();
}

void WebAccessStage::slotRigTimeout()
{
    m_serial++;
    emit broadcast(QString("VIS|RIG_CHANGED|%1").arg(m_serial));
}

/*********************************************************************
 * Stage and prop files
 *********************************************************************/

QString WebAccessStage::stageFilePath(const QString &showPath)
{
    if (showPath.isEmpty())
        return QString();

    QFileInfo show(showPath);
    return QDir(show.absolutePath()).absoluteFilePath(show.completeBaseName() + ".stage.json");
}

QString WebAccessStage::propsFilePath()
{
    QDir userDir = QLCFile::userDirectory(QString(USERQLCPLUSDIR), QString(USERQLCPLUSDIR), QStringList());
    return userDir.absoluteFilePath("stage-props.json");
}

QString WebAccessStage::readJsonFile(const QString &path, const QString &key, int rev) const
{
    QJsonObject root;
    root["path"] = QDir::toNativeSeparators(path);
    root["rev"] = rev;
    root[key] = QJsonValue();

    QFile file(path);
    root["exists"] = file.exists();
    if (file.exists())
    {
        if (!file.open(QIODevice::ReadOnly))
        {
            root["error"] = QString("cannot read %1").arg(file.errorString());
        }
        else
        {
            QJsonParseError err;
            QJsonDocument doc = QJsonDocument::fromJson(file.readAll(), &err);
            file.close();
            if (err.error != QJsonParseError::NoError || !doc.isObject())
                root["error"] = QString("not valid JSON: %1").arg(err.errorString());
            else
                root[key] = doc.object();
        }
    }

    return QString::fromUtf8(QJsonDocument(root).toJson(QJsonDocument::Compact));
}

QString WebAccessStage::writeJsonFile(const QString &path, const QString &json, int &rev)
{
    if (json.size() > MAX_JSON_FILE_SIZE)
        return QString("ERR|file too large");

    QJsonParseError err;
    QJsonDocument doc = QJsonDocument::fromJson(json.toUtf8(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject())
        return QString("ERR|not valid JSON: %1").arg(err.errorString());
    if (!doc.object().contains("version"))
        return QString("ERR|missing version");

    // keep the previous file as .bak
    if (QFile::exists(path))
    {
        QString bak = path + ".bak";
        QFile::remove(bak);
        if (!QFile::copy(path, bak))
            qWarning() << "[stage] could not back up" << path;
    }

    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly))
        return QString("ERR|cannot write %1: %2").arg(QDir::toNativeSeparators(path)).arg(file.errorString());
    file.write(doc.toJson(QJsonDocument::Indented));
    if (!file.commit())
        return QString("ERR|cannot write %1: %2").arg(QDir::toNativeSeparators(path)).arg(file.errorString());

    rev++;
    return QString("OK|%1").arg(rev);
}

QString WebAccessStage::stageJson(const QString &showPath) const
{
    QString path = stageFilePath(showPath);
    if (path.isEmpty())
        return QString("ERR|the show has not been saved to a file yet");

    return readJsonFile(path, "stage", m_stageRev);
}

QString WebAccessStage::saveStage(const QString &showPath, const QString &json)
{
    QString path = stageFilePath(showPath);
    if (path.isEmpty())
        return QString("ERR|save the show to a file first");

    QString result = writeJsonFile(path, json, m_stageRev);
    if (result.startsWith("OK"))
        emit broadcast(QString("VIS|STAGE_SAVED|%1").arg(m_stageRev));
    return result;
}

QString WebAccessStage::propsJson() const
{
    return readJsonFile(propsFilePath(), "props", m_propsRev);
}

QString WebAccessStage::saveProps(const QString &json)
{
    QString result = writeJsonFile(propsFilePath(), json, m_propsRev);
    if (result.startsWith("OK"))
        emit broadcast(QString("VIS|PROPS_SAVED|%1").arg(m_propsRev));
    return result;
}

/*********************************************************************
 * Static files
 *********************************************************************/

QString WebAccessStage::mimeType(const QString &fileName)
{
    QString ext = QFileInfo(fileName).suffix().toLower();

    if (ext == "json")
        return "application/json";
    if (ext == "glb")
        return "model/gltf-binary";
    if (ext == "gltf")
        return "model/gltf+json";
    if (ext == "bin" || ext == "3ds")
        return "application/octet-stream";
    if (ext == "dae")
        return "model/vnd.collada+xml";
    if (ext == "png")
        return "image/png";
    if (ext == "jpg" || ext == "jpeg")
        return "image/jpeg";
    if (ext == "svg")
        return "image/svg+xml";

    return QString();
}

QString WebAccessStage::safeJoin(const QString &root, const QString &relPath)
{
    if (root.isEmpty() || relPath.isEmpty() || relPath.contains('\\') || relPath.contains(':') ||
        relPath.startsWith('/'))
        return QString();

    foreach (QString part, relPath.split('/'))
    {
        if (part == ".." || part == ".")
            return QString();
    }

    QString rootPath = QFileInfo(root).canonicalFilePath();
    QString filePath = QFileInfo(QDir(root).absoluteFilePath(relPath)).canonicalFilePath();
    if (rootPath.isEmpty() || filePath.isEmpty() || !filePath.startsWith(rootPath + "/"))
        return QString();

    return filePath;
}

/*********************************************************************
 * DMX stream
 *********************************************************************/

QList<QByteArray> WebAccessStage::readUniverses() const
{
    QList<QByteArray> frames;
    InputOutputMap *ioMap = m_doc->inputOutputMap();

    // one short lock per tick; the values are post Grand Master, as sent to the rig
    QList<Universe*> universes = ioMap->claimUniverses();
    foreach (Universe *uni, universes)
    {
        const QByteArray *values = uni->postGMValues();
        frames.append(values ? QByteArray(values->constData(), values->size()) : QByteArray());
    }
    ioMap->releaseUniverses(false);

    return frames;
}

void WebAccessStage::subscribe(QHttpConnection *conn)
{
    if (conn == NULL || m_subscribers.contains(conn))
        return;

    m_subscribers.append(conn);
    connect(conn, &QObject::destroyed, this, [this, conn]() { m_subscribers.removeAll(conn); });

    // a full snapshot for the new page
    QList<QByteArray> frames = readUniverses();
    for (int i = 0; i < frames.count(); i++)
        conn->webSocketWrite(QString("VIS|DMX|%1|%2").arg(i).arg(QString::fromLatin1(frames.at(i).toBase64())), false);

    if (!m_dmxTimer->isActive())
    {
        m_lastFrames = frames;
        m_dmxTimer->start();
    }
}

void WebAccessStage::unsubscribe(QHttpConnection *conn)
{
    m_subscribers.removeAll(conn);
    if (m_subscribers.isEmpty())
        m_dmxTimer->stop();
}

void WebAccessStage::relay(const QString &message) const
{
    foreach (QHttpConnection *conn, m_subscribers)
        conn->webSocketWrite(message, false);
}

void WebAccessStage::slotDmxTick()
{
    if (m_subscribers.isEmpty())
    {
        m_dmxTimer->stop();
        return;
    }

    QList<QByteArray> frames = readUniverses();
    for (int i = 0; i < frames.count(); i++)
    {
        if (i < m_lastFrames.count() && m_lastFrames.at(i) == frames.at(i))
            continue;

        relay(QString("VIS|DMX|%1|%2").arg(i).arg(QString::fromLatin1(frames.at(i).toBase64())));
    }
    m_lastFrames = frames;
}
